import * as crypto from 'crypto';
import * as path from 'path';
import * as cdk from 'aws-cdk-lib/core';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as apigwv2Authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as apigwv2Integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';

export interface WebStackProps extends cdk.StackProps {
  /** Written by the scoring pipeline in InfraStack; read (only) by the API here. */
  readonly scoredJobsTable: dynamodb.Table;
  /** profile.yaml / settings.yaml. The settings page reads and writes these. */
  readonly configBucket: s3.Bucket;
}

/** SSM parameter holding the comma-separated list of emails allowed to register. */
const ALLOWLIST_PARAMETER = '/job-ad-agent/auth/allowed-emails';
/** SSM parameter holding the Google OAuth client id (not secret). */
const GOOGLE_CLIENT_ID_PARAMETER = '/job-ad-agent/auth/google-client-id';

/**
 * Phase 4: the viewer on the internet, behind Google sign-in.
 *
 * Separate from InfraStack on purpose. The scoring pipeline and the website
 * change at completely different rates, and a broken frontend deploy should
 * never be able to roll back the thing that collects the data. The only link
 * between them is the DynamoDB table, passed in as a prop.
 *
 * Shape:
 *   CloudFront ──► S3            the static viewer
 *   browser    ──► Cognito       managed login, Google as the only IdP
 *   browser    ──► API Gateway   JWT-authorized, ──► Lambda ──► DynamoDB (read)
 *
 * WHY THE API IS NOT BEHIND CLOUDFRONT
 * Routing /api/* through CloudFront would give one origin and no CORS, which
 * is nicer. It is also impossible here, and the reason is worth recording: the
 * user pool client needs CloudFront's domain for its OAuth callback, the API
 * Gateway authorizer needs that client's id, and CloudFront would need the API
 * as an origin. That is a genuine CloudFormation dependency cycle, not a CDK
 * ordering problem. Splitting the origins breaks it, and the cost is one CORS
 * rule. The alternative — validating JWTs by hand inside the Lambda so the
 * gateway needs no authorizer — trades a managed security control for our own
 * crypto code, which is the wrong trade.
 */
export class WebStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: WebStackProps) {
    super(scope, id, props);

    // --- Google OAuth credentials ----------------------------------------

    // The client id is not secret, so it comes straight from SSM.
    const googleClientId = ssm.StringParameter.valueForStringParameter(
      this, GOOGLE_CLIENT_ID_PARAMETER,
    );

    // The client SECRET cannot. CloudFormation's {{resolve:ssm-secure:...}}
    // only works on a short allowlist of resource properties (RDS passwords,
    // IAM login profiles and a few others); AWS::Cognito::UserPoolIdentityProvider
    // is NOT on it. Using SecretValue.ssmSecure() here would store the literal
    // unresolved string as the Google secret and sign-in would fail with
    // nothing useful in the logs. Secrets Manager would resolve, at $0.40 per
    // secret per month.
    //
    // So it arrives as a noEcho stack parameter instead: SSM stays the single
    // source of truth, the deploy command reads it from there, and the value
    // never lands in git or cdk.out. See the README for the exact command.
    const googleClientSecret = new cdk.CfnParameter(this, 'GoogleClientSecret', {
      type: 'String',
      noEcho: true,
      minLength: 1,
      description:
        'Google OAuth client secret. Pass from SSM at deploy time; never commit it.',
    });

    // --- The gate --------------------------------------------------------

    // "Sign in with Google" means every Google account on earth can reach the
    // login page. This trigger is what stops that from meaning they can all
    // register. Allowlist lives in SSM so adding a friend needs no redeploy.
    const preSignUpFn = new lambda.Function(this, 'PreSignUpAllowlist', {
      functionName: 'job-ad-agent-presignup-allowlist',
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'index.handler',
      // exclude __pycache__: running the test suite imports this module and
      // leaves .pyc files beside it, which would otherwise be bundled into the
      // deployed asset AND change its content hash — making the function
      // redeploy or not depending on whether tests had been run locally.
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'presignup'), {
        exclude: ['__pycache__'],
      }),
      timeout: cdk.Duration.seconds(10),
      memorySize: 128,
      environment: { ALLOWLIST_PARAMETER },
      description: 'Rejects Cognito sign-ups for emails not on the SSM allowlist',
    });

    // Exactly one parameter, not the whole path. This function has no business
    // reading the Adzuna or OpenRouter keys that live next door.
    preSignUpFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter'],
      resources: [
        `arn:aws:ssm:${this.region}:${this.account}:parameter${ALLOWLIST_PARAMETER}`,
      ],
    }));

    // --- Identity --------------------------------------------------------

    const userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'job-ad-agent-users',

      // Pinned, not left to default. ESSENTIALS is what managed login requires
      // and it carries a perpetual 10,000 monthly-active-user free tier, which
      // social sign-ins (Google included) draw from at the same rate as native
      // ones. PLUS has NO free tier, so drifting onto it would start billing
      // from the first user.
      featurePlan: cognito.FeaturePlan.ESSENTIALS,

      // Required for federated users: Cognito creates the user record on first
      // Google sign-in, and that path is blocked when only admins may create
      // users. The PreSignUp trigger above is what makes this safe.
      selfSignUpEnabled: true,

      signInAliases: { email: true },
      standardAttributes: { email: { required: true, mutable: true } },
      autoVerify: { email: true },
      lambdaTriggers: { preSignUp: preSignUpFn },

      // Retained deliberately: destroying the pool destroys the user records,
      // and a stack replacement should not silently sign everyone out.
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const googleProvider = new cognito.UserPoolIdentityProviderGoogle(this, 'Google', {
      userPool,
      clientId: googleClientId,
      clientSecretValue: cdk.SecretValue.cfnParameter(googleClientSecret),

      // MUST include 'email'. The CDK default is ['profile'] alone, and with
      // that the PreSignUp trigger receives no email attribute — so it would
      // fail closed and deny every single sign-in, including yours.
      scopes: ['openid', 'email', 'profile'],

      // Without this mapping Google's claims never reach the Cognito user, and
      // 'email' stays empty for the same reason.
      attributeMapping: {
        email: cognito.ProviderAttribute.GOOGLE_EMAIL,
        givenName: cognito.ProviderAttribute.GOOGLE_GIVEN_NAME,
        familyName: cognito.ProviderAttribute.GOOGLE_FAMILY_NAME,
      },
    });

    // Derived from a hash rather than the raw account id, because unlike the
    // private config bucket this hostname is visible to every person who signs
    // in. Deterministic for a given account+region, so redeploys keep the same
    // login URL and the Google console entry never has to change.
    //
    // The hash is computed here in Node at synth time, which means it needs a
    // REAL account id. If credentials are missing, this.account is an
    // unresolved CDK token and we would silently hash the literal string
    // "${Token[AWS.AccountId.0]}" — producing a different login domain than a
    // credentialed synth, replacing the Cognito domain and breaking the URI
    // already registered in the Google console. Fail loudly instead.
    const configuredPrefix = this.node.tryGetContext('cognitoDomainPrefix') as
      | string
      | undefined;
    if (!configuredPrefix && cdk.Token.isUnresolved(this.account)) {
      throw new Error(
        'WebStack needs a concrete AWS account to derive a stable Cognito ' +
        'domain prefix. Authenticate (so CDK_DEFAULT_ACCOUNT is set), or pass ' +
        '-c cognitoDomainPrefix=<your-prefix> to synth without credentials.',
      );
    }
    const domainPrefix =
      configuredPrefix ??
      `job-ad-agent-${crypto
        .createHash('sha256')
        .update(`${this.account}/${this.region}`)
        .digest('hex')
        .slice(0, 10)}`;

    const userPoolDomain = userPool.addDomain('LoginDomain', {
      cognitoDomain: { domainPrefix },
      // Version 2 is the newer managed login with the branding designer.
      managedLoginVersion: cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
    });

    // --- Frontend --------------------------------------------------------

    // DESTROY, unlike the config and data buckets in InfraStack. Everything in
    // here is rebuilt from git on the next deploy, so retaining it would just
    // orphan buckets.
    const siteBucket = new s3.Bucket(this, 'SiteBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const distribution = new cloudfront.Distribution(this, 'SiteDistribution', {
      comment: 'job-ad-agent viewer',
      defaultRootObject: 'index.html',
      defaultBehavior: {
        // OAC, not the legacy OAI: the bucket stays fully private and only
        // this distribution can read it.
        origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      // PRICE_CLASS_100 is the cheap default and would be actively wrong here:
      // it excludes Australia and New Zealand, so the one person who actually
      // uses this site would be served from the other side of the planet. At
      // this traffic volume the price difference is rounding error.
      priceClass: cloudfront.PriceClass.PRICE_CLASS_ALL,
      errorResponses: [
        // A private S3 origin answers unknown keys with 403, not 404. Serving
        // index.html keeps a mistyped path on the app instead of on an XML
        // error page.
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html' },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html' },
      ],
    });

    const siteUrl = `https://${distribution.distributionDomainName}`;

    // --- API -------------------------------------------------------------

    const apiFn = new lambda.DockerImageFunction(this, 'ViewerApiFunction', {
      functionName: 'job-ad-agent-viewer-api',

      // The SAME image asset as the scoring function. cmd/entrypoint are
      // applied to the Lambda's image config rather than baked into the image,
      // so the content hash matches, Docker builds once and ECR stores one
      // copy. Two entry points, one artefact.
      code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, '..', '..'), {
        platform: ecrAssets.Platform.LINUX_ARM64,
        cmd: ['api_handler.handler'],
      }),
      architecture: lambda.Architecture.ARM_64,

      memorySize: 512,
      // API Gateway HTTP APIs cap integration time at 30s anyway.
      timeout: cdk.Duration.seconds(30),

      environment: {
        STORE_BACKEND: 'dynamodb',
        STORE_TABLE_NAME: props.scoredJobsTable.tableName,
        // Makes config_store resolve to S3 rather than local files, so the
        // settings page edits the same documents the scoring run reads.
        CONFIG_BUCKET: props.configBucket.bucketName,
      },
      description: 'Viewer API and settings editor (FastAPI via Mangum)',
    });

    // Read, not read-write. This function serves a dashboard; nothing it does
    // should ever be able to modify a SCORE.
    props.scoredJobsTable.grantReadData(apiFn);

    // Config is the one thing it may write, because that is the whole point of
    // the settings page. grantRead + grantPut rather than grantReadWrite: the
    // latter also grants s3:DeleteObject, and nothing in the API deletes a
    // config document. Versioning on the bucket is the undo.
    props.configBucket.grantRead(apiFn);
    props.configBucket.grantPut(apiFn);

    const userPoolClient = userPool.addClient('WebClient', {
      userPoolClientName: 'job-ad-agent-web',

      // Public browser client: no secret to keep, PKCE proves the caller.
      generateSecret: false,

      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [
          cognito.OAuthScope.OPENID,
          cognito.OAuthScope.EMAIL,
          cognito.OAuthScope.PROFILE,
        ],
        // Both forms registered because Cognito matches redirect_uri exactly,
        // and whether the browser sends a trailing slash is not worth an
        // afternoon of redirect_mismatch debugging.
        callbackUrls: [siteUrl, `${siteUrl}/`],
        logoutUrls: [siteUrl, `${siteUrl}/`],
      },

      // Google only. This is what makes the app federated-only in practice:
      // native username/password sign-in is simply not offered by this client,
      // so no password ever exists to be stored or leaked.
      supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.GOOGLE],

      preventUserExistenceErrors: true,
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
    });

    // CloudFormation will happily create the client before the Google provider
    // exists and then fail, because the client references an IdP that is not
    // there yet. CDK cannot infer this one.
    userPoolClient.node.addDependency(googleProvider);

    // Managed login v2 renders from a branding style attached to the app
    // client. Rather than depend on whether Cognito synthesises a default for
    // a CloudFormation-created domain, ask for the Cognito-provided defaults
    // explicitly — the failure mode otherwise is a login page that breaks at
    // sign-in time, long after a green deploy.
    //
    // useCognitoProvidedValues requires that settings and assets are BOTH
    // omitted. Customise later in the console branding editor if you want.
    new cognito.CfnManagedLoginBranding(this, 'LoginBranding', {
      userPoolId: userPool.userPoolId,
      clientId: userPoolClient.userPoolClientId,
      useCognitoProvidedValues: true,
    });

    const httpApi = new apigwv2.HttpApi(this, 'ViewerApi', {
      apiName: 'job-ad-agent-viewer',
      description: 'Read-only scored-jobs API for the web viewer',
      corsPreflight: {
        // Only the site may call the API from a browser. Note that API Gateway
        // answers the OPTIONS preflight itself WITHOUT running the authorizer,
        // which is what stops preflights from failing 401.
        allowOrigins: [siteUrl],
        allowHeaders: ['authorization', 'content-type'],
        allowMethods: [
          apigwv2.CorsHttpMethod.GET,
          apigwv2.CorsHttpMethod.PUT,
          apigwv2.CorsHttpMethod.OPTIONS,
        ],
        maxAge: cdk.Duration.hours(1),
      },
    });

    httpApi.addRoutes({
      path: '/api/{proxy+}',
      // PUT for the settings and profile editors. Both are behind the same
      // JWT authorizer as the reads: there is no unauthenticated write.
      methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.PUT],
      integration: new apigwv2Integrations.HttpLambdaIntegration('ApiIntegration', apiFn),
      authorizer: new apigwv2Authorizers.HttpUserPoolAuthorizer(
        'ViewerAuthorizer', userPool, {
          userPoolClients: [userPoolClient],
          // The frontend sends the ID token: the authorizer validates 'aud'
          // against these client ids, and a Cognito ACCESS token carries
          // 'client_id' instead of 'aud'. The ID token also carries the email
          // claim the API logs.
          identitySource: ['$request.header.Authorization'],
        },
      ),
    });

    // --- Ship the frontend ------------------------------------------------

    // The browser cannot know the pool id, client id, login domain or API URL
    // until they exist, so they are written into the bucket at deploy time
    // rather than hardcoded. None of these are secrets — a public OAuth client
    // id is public by design.
    new s3deploy.BucketDeployment(this, 'DeploySite', {
      sources: [
        s3deploy.Source.asset(path.join(__dirname, '..', '..', 'static')),
        s3deploy.Source.jsonData('config.json', {
          region: this.region,
          userPoolId: userPool.userPoolId,
          userPoolClientId: userPoolClient.userPoolClientId,
          cognitoDomain: userPoolDomain.baseUrl(),
          apiBaseUrl: httpApi.apiEndpoint,
        }),
      ],
      destinationBucket: siteBucket,
      // Without an invalidation the old bundle stays cached at the edge and a
      // deploy appears to do nothing.
      distribution,
      distributionPaths: ['/*'],
    });

    // --- Outputs ---------------------------------------------------------

    new cdk.CfnOutput(this, 'SiteUrl', {
      value: siteUrl,
      description: 'Open this in a browser',
    });
    new cdk.CfnOutput(this, 'GoogleRedirectUri', {
      value: `${userPoolDomain.baseUrl()}/oauth2/idpresponse`,
      description:
        'Paste into Google Cloud Console > Credentials > Authorised redirect URIs',
    });
    new cdk.CfnOutput(this, 'GoogleJavaScriptOrigin', {
      value: userPoolDomain.baseUrl(),
      description:
        'Paste into Google Cloud Console > Credentials > Authorised JavaScript origins',
    });
    new cdk.CfnOutput(this, 'LoginUrl', {
      value: userPoolDomain.baseUrl(),
      description: 'Cognito managed login domain',
    });
    new cdk.CfnOutput(this, 'ApiEndpoint', {
      value: httpApi.apiEndpoint,
      description: 'Viewer API base URL (requires a Cognito ID token)',
    });
    new cdk.CfnOutput(this, 'UserPoolIdOut', { value: userPool.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientIdOut', {
      value: userPoolClient.userPoolClientId,
    });
    new cdk.CfnOutput(this, 'AllowlistParameter', {
      value: ALLOWLIST_PARAMETER,
      description: 'Comma-separated emails permitted to sign up',
    });
  }
}
