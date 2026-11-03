import * as cdk from 'aws-cdk-lib/core';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { InfraStack } from '../lib/infra-stack';
import { WebStack } from '../lib/web-stack';

// These stacks encode security and cost decisions that are invisible at
// runtime until they are wrong: an allowlist trigger that is not wired up, an
// API route without an authorizer, a table that a redeploy would delete. Each
// test below pins one of those.
//
// A concrete account is supplied because WebStack derives its Cognito domain
// prefix from a real account id and throws on an unresolved token by design.
const env = { account: '123456789012', region: 'ap-southeast-2' };

function stacks() {
  const app = new cdk.App();
  const infra = new InfraStack(app, 'TestInfra', { env });
  const web = new WebStack(app, 'TestWeb', {
    env,
    scoredJobsTable: infra.scoredJobsTable,
    configBucket: infra.configBucket,
  });
  return { infra: Template.fromStack(infra), web: Template.fromStack(web) };
}


/**
 * Every IAM action granted to ONE function's role.
 *
 * Scanning every policy in the stack is not good enough: the site bucket's
 * autoDeleteObjects custom resource holds s3:DeleteObject* on its own role,
 * so a stack-wide search reports permissions this function does not have.
 * Resolve the function's role and read only the policies attached to it.
 */
function actionsFor(template: Template, functionName: string): string[] {
  const fns = template.findResources('AWS::Lambda::Function');
  const fn = Object.values(fns).find(
    (f: any) => f.Properties.FunctionName === functionName,
  ) as any;
  if (!fn) throw new Error(`no function named ${functionName}`);

  const roleId = fn.Properties.Role['Fn::GetAtt'][0];
  const policies = template.findResources('AWS::IAM::Policy');
  return Object.values(policies)
    .filter((p: any) =>
      (p.Properties.Roles || []).some((r: any) => r.Ref === roleId),
    )
    .flatMap((p: any) =>
      p.Properties.PolicyDocument.Statement.flatMap((st: any) =>
        Array.isArray(st.Action) ? st.Action : [st.Action],
      ),
    );
}

describe('InfraStack', () => {
  test('the scoring function is capped at one concurrent execution', () => {
    // Two overlapping runs both miss the cache and both pay the LLM for the
    // same postings. The schedule retries twice, so this cap is what makes
    // "one run at a time" true rather than merely intended.
    stacks().infra.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'job-ad-agent-scoring',
      ReservedConcurrentExecutions: 1,
    });
  });

  test('the scored-jobs table survives a stack replacement', () => {
    stacks().infra.hasResource('AWS::DynamoDB::Table', {
      DeletionPolicy: 'Retain',
    });
  });

  test('the scoring function reads its config from the paths aws_config writes', () => {
    // aws_config.py downloads to whatever these name. If they drift, the
    // download lands where nothing reads it and only Lambda notices.
    stacks().infra.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'job-ad-agent-scoring',
      Environment: {
        Variables: Match.objectLike({
          PROFILE_PATH: '/tmp/profile.yaml',
          COMPANIES_PATH: '/tmp/companies.yaml',
          STORE_BACKEND: 'dynamodb',
        }),
      },
    });
  });

  test('SSM access is scoped to this app, not all of Parameter Store', () => {
    stacks().infra.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: Match.arrayWith(['ssm:GetParametersByPath']),
            Resource: Match.arrayWith([
              Match.stringLikeRegexp('parameter/job-ad-agent\\*?$'),
            ]),
          }),
        ]),
      }),
    });
  });
});

describe('WebStack', () => {
  test('the PreSignUp allowlist trigger is wired to the user pool', () => {
    // Without this the app is "anyone with a Google account may register".
    stacks().web.hasResourceProperties('AWS::Cognito::UserPool', {
      LambdaConfig: Match.objectLike({ PreSignUp: Match.anyValue() }),
    });
  });

  test('the allowlist function can read only the allowlist parameter', () => {
    stacks().web.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: 'ssm:GetParameter',
            Resource: Match.stringLikeRegexp('auth/allowed-emails$'),
          }),
        ]),
      }),
    });
  });

  test('the Google provider requests the email scope', () => {
    // The CDK default is ['profile'] alone, which leaves PreSignUp with no
    // email to check and so denies every sign-in, including the owner's.
    stacks().web.hasResourceProperties('AWS::Cognito::UserPoolIdentityProvider', {
      ProviderName: 'Google',
      ProviderDetails: Match.objectLike({ authorize_scopes: 'openid email profile' }),
    });
  });

  test('every API route is behind the JWT authorizer', () => {
    const web = stacks().web;
    const routes = web.findResources('AWS::ApiGatewayV2::Route');
    const names = Object.keys(routes);
    expect(names.length).toBeGreaterThan(0);
    for (const name of names) {
      expect(routes[name].Properties.AuthorizationType).toBe('JWT');
      expect(routes[name].Properties.AuthorizerId).toBeDefined();
    }
  });

  test('the viewer API is granted read access but not write', () => {
    const actions = actionsFor(stacks().web, 'job-ad-agent-viewer-api');
    expect(actions).toContain('dynamodb:GetItem');
    expect(actions.filter((a) => a.startsWith('dynamodb:Put'))).toEqual([]);
    expect(actions.filter((a) => a.startsWith('dynamodb:Delete'))).toEqual([]);
    expect(actions.filter((a) => a.startsWith('dynamodb:Update'))).toEqual([]);
  });

  test('the API can write config but never delete it', () => {
    // The settings page needs PutObject. It must not acquire DeleteObject on
    // the way: grantReadWrite would have handed that over silently.
    const actions = actionsFor(stacks().web, 'job-ad-agent-viewer-api');
    expect(actions).toContain('s3:PutObject');
    expect(actions.some((a) => a.startsWith('s3:GetObject'))).toBe(true);
    expect(actions.filter((a) => a.startsWith('s3:DeleteObject'))).toEqual([]);
  });

  test('the API function is told which config bucket to use', () => {
    // Without CONFIG_BUCKET, config_store falls back to LocalConfigStore and
    // the settings page would silently edit a file inside the Lambda's own
    // container -- saving "successfully" and losing the edit on the next boot.
    stacks().web.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'job-ad-agent-viewer-api',
      Environment: { Variables: Match.objectLike({ CONFIG_BUCKET: Match.anyValue() }) },
    });
  });

  test('the settings write path is authorized, not just the reads', () => {
    const routes = stacks().web.findResources('AWS::ApiGatewayV2::Route');
    const keys = Object.values(routes).map((r: any) => r.Properties.RouteKey);
    expect(keys.some((k: string) => k.startsWith('PUT '))).toBe(true);
    for (const r of Object.values(routes)) {
      expect((r as any).Properties.AuthorizationType).toBe('JWT');
    }
  });

  test('every site response carries the security headers', () => {
    stacks().web.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        DefaultCacheBehavior: Match.objectLike({
          ResponseHeadersPolicyId: Match.anyValue(),
        }),
      }),
    });
  });

  test('the CSP forbids inline script and foreign script origins', () => {
    // The page renders job-ad text. If an escaping bug ever lets markup
    // through, this policy is what stops it from running.
    const policies = stacks().web.findResources('AWS::CloudFront::ResponseHeadersPolicy');
    const configs = Object.values(policies).map(
      (p: any) => p.Properties.ResponseHeadersPolicyConfig,
    );
    expect(configs).toHaveLength(1);

    const headers = configs[0].SecurityHeadersConfig;
    const csp = JSON.stringify(headers.ContentSecurityPolicy.ContentSecurityPolicy);
    expect(csp).toContain("script-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).not.toContain('unsafe-inline');
    expect(csp).not.toContain('unsafe-eval');
    // The two services the page calls: Cognito and API Gateway.
    expect(csp).toContain('amazoncognito.com');
    expect(csp).toContain('execute-api');

    expect(headers.StrictTransportSecurity.AccessControlMaxAgeSec).toBeGreaterThanOrEqual(31536000);
    expect(headers.FrameOptions.FrameOption).toBe('DENY');
  });

  test('the API is throttled', () => {
    stacks().web.hasResourceProperties('AWS::ApiGatewayV2::Stage', {
      DefaultRouteSettings: Match.objectLike({
        ThrottlingRateLimit: Match.anyValue(),
        ThrottlingBurstLimit: Match.anyValue(),
      }),
    });
  });

  test('the site bucket is private', () => {
    stacks().web.hasResourceProperties('AWS::S3::Bucket', {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });
  });
});
