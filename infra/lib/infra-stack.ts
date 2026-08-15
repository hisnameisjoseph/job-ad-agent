import * as cdk from 'aws-cdk-lib/core';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';

/**
 * Infrastructure for job-ad-agent.
 *
 * Phase 2 is deliberately just the data layer. The Lambda that writes to this
 * table arrives in Phase 3; keeping them separate means a broken function
 * definition can never take the table (and every paid-for score) with it.
 *
 * API keys are NOT defined here. They live in SSM Parameter Store, created out
 * of band, so secrets never enter infrastructure code or git history.
 */
export class InfraStack extends cdk.Stack {
  public readonly scoredJobsTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.scoredJobsTable = new dynamodb.Table(this, 'ScoredJobs', {
      tableName: 'job-ad-agent-scored-jobs',

      // "source#id", because ids are only unique within a provider and this
      // pipeline now runs four of them.
      partitionKey: { name: 'job_key', type: dynamodb.AttributeType.STRING },

      // On-demand: this table sees a few hundred writes a day at most, so
      // provisioned capacity would cost more and need tuning.
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,

      // Dead postings evict themselves. This is why ScoredJob needed a
      // timestamp — without one there is nothing to expire on.
      timeToLiveAttribute: 'expires_at',

      // RETAIN: `cdk destroy` must never delete scores that cost real money to
      // regenerate. Trade-off: after a destroy, the table survives and a fresh
      // deploy fails with "already exists" until you delete it by hand.
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    new cdk.CfnOutput(this, 'ScoredJobsTableName', {
      value: this.scoredJobsTable.tableName,
      description: 'Set STORE_TABLE_NAME to this value',
    });
  }
}
