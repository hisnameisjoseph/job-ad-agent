import * as path from 'path';
import * as cdk from 'aws-cdk-lib/core';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import { Construct } from 'constructs';

/**
 * Infrastructure for job-ad-agent.
 *
 * Phase 3 adds the compute: a container-packaged Lambda on a daily schedule,
 * reading its secrets from SSM and its profile from S3.
 *
 * API keys are still NOT defined here. They live in SSM Parameter Store,
 * created out of band, so secrets never enter infrastructure code or git.
 */
export class InfraStack extends cdk.Stack {
  public readonly scoredJobsTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // --- Data ------------------------------------------------------------

    this.scoredJobsTable = new dynamodb.Table(this, 'ScoredJobs', {
      tableName: 'job-ad-agent-scored-jobs',
      partitionKey: { name: 'job_key', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires_at',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Versioned so a bad profile edit can be rolled back, and because the
    // whole point of S3 here is editing without redeploying.
    const configBucket = new s3.Bucket(this, 'ConfigBucket', {
      bucketName: `job-ad-agent-config-${this.account}`,
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // --- Compute ---------------------------------------------------------

    const scoringFn = new lambda.DockerImageFunction(this, 'ScoringFunction', {
      functionName: 'job-ad-agent-scoring',

      // Build context is the repo root, two levels up from infra/lib.
      code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, '..', '..')),

      // 256MB: the work is network-bound, but Lambda scales CPU with memory,
      // so 128MB can make the run longer AND more expensive.
      memorySize: 256,
      timeout: cdk.Duration.minutes(15),

      // One run at a time. Two overlapping runs would double-score the same
      // new postings before either had written them.
      reservedConcurrentExecutions: 1,

      environment: {
        STORE_BACKEND: 'dynamodb',
        STORE_TABLE_NAME: this.scoredJobsTable.tableName,
        CONFIG_BUCKET: configBucket.bucketName,
        PROFILE_PATH: '/tmp/profile.yaml',
        COMPANIES_PATH: '/tmp/companies.yaml',
        SSM_PREFIX: '/job-ad-agent',
        MAX_JOBS_PER_RUN: '150',
        SCORING_CONCURRENCY: '5',
        // NOTE: never set AWS_REGION here. Lambda reserves it and injects it
        // automatically; setting it makes the deployment fail.
      },
    });

    // --- Permissions (least privilege) -----------------------------------

    this.scoredJobsTable.grantReadWriteData(scoringFn);
    configBucket.grantRead(scoringFn);

    // Scoped to this app's parameter path, not all of SSM. Decryption of the
    // SecureStrings is handled by the AWS-managed aws/ssm key, so no explicit
    // KMS grant is needed.
    scoringFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'ssm:GetParameters', 'ssm:GetParametersByPath'],
      resources: [
        `arn:aws:ssm:${this.region}:${this.account}:parameter/job-ad-agent`,
        `arn:aws:ssm:${this.region}:${this.account}:parameter/job-ad-agent/*`,
      ],
    }));

    // --- Schedule --------------------------------------------------------

    const schedulerRole = new iam.Role(this, 'SchedulerRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      description: 'Lets EventBridge Scheduler invoke the scoring function',
    });
    scoringFn.grantInvoke(schedulerRole);

    // EventBridge Scheduler rather than an Events rule, because it takes a
    // timezone directly. An Events cron is UTC-only, so the run would drift
    // by an hour every time Melbourne switches to and from daylight saving.
    new scheduler.CfnSchedule(this, 'DailyScoringSchedule', {
      name: 'job-ad-agent-daily',
      description: 'Daily job scoring run',
      flexibleTimeWindow: { mode: 'OFF' },
      scheduleExpression: 'cron(0 7 * * ? *)',
      scheduleExpressionTimezone: 'Australia/Melbourne',
      target: {
        arn: scoringFn.functionArn,
        roleArn: schedulerRole.roleArn,
        retryPolicy: {
          maximumRetryAttempts: 2,
          maximumEventAgeInSeconds: 3600,
        },
      },
    });

    // --- Outputs ---------------------------------------------------------

    new cdk.CfnOutput(this, 'ScoredJobsTableName', {
      value: this.scoredJobsTable.tableName,
    });
    new cdk.CfnOutput(this, 'ConfigBucketName', {
      value: configBucket.bucketName,
      description: 'Upload profile.yaml and companies.yaml here',
    });
    new cdk.CfnOutput(this, 'ScoringFunctionName', {
      value: scoringFn.functionName,
    });
  }
}