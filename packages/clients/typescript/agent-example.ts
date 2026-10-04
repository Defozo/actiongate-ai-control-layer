// Node 24 can execute this file with native TypeScript stripping.
import { ActionGate } from './actiongate.ts';

const token = process.env.ACTIONGATE_WORKLOAD_TOKEN;
const runId = process.env.ACTIONGATE_RUN_ID;
if (!token || !runId) throw new Error('The trusted application must inject a run-bound workload identity.');
const client = new ActionGate(process.env.ACTIONGATE_BASE_URL ?? 'http://127.0.0.1:8080', token, runId);
const result = await client.action('documents.read', { document_id: process.env.ACTIONGATE_DOCUMENT_ID ?? 'supplier-acme-1' });
console.log({ id: result.id, decision: result.decision, status: result.status, classification: result.label });
if (result.status !== 'completed') process.exitCode = 1;
