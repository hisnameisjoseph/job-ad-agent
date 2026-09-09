#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import { InfraStack } from '../lib/infra-stack';
import { WebStack } from '../lib/web-stack';

const app = new cdk.App();

// Region is pinned rather than inherited from the environment, so a stale
// AWS_REGION can never quietly deploy this into us-east-1.
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: 'ap-southeast-2',
};

// Phase 3: ingestion, scoring, storage, schedule.
const infra = new InfraStack(app, 'InfraStack', {
  env,
  description: 'job-ad-agent: scored-job storage',
});

// Phase 4: the viewer on the internet, behind Google sign-in.
//
// A separate stack because the website and the pipeline change at different
// rates and should fail independently. The table is passed explicitly, so the
// dependency between them is one visible line rather than an implicit lookup.
new WebStack(app, 'WebStack', {
  env,
  description: 'job-ad-agent: authenticated web viewer (Cognito + API + CDN)',
  scoredJobsTable: infra.scoredJobsTable,
  configBucket: infra.configBucket,
});
