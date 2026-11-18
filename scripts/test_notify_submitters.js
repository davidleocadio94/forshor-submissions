'use strict';
// Tests for the submitter notifications (scripts/ci/notify_submitters.js, .github/workflows/notify-submitters.yml).
// Run: node --test scripts/test_notify_submitters.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const notify = require('./ci/notify_submitters.js');

const HEAD = 'a'.repeat(40), OTHER = 'b'.repeat(40);
const REPO = 'zapatacomputing/forshor-submissions';
const SOURCE = '.github/workflows/table-generation-submission.yml';
const RUN_URL = `https://github.com/${REPO}/actions/runs/123/attempts/1`;
const ZWSP = '\u200b';

const result = (checks, extra = {}) => ({ schema_version: 1, kind: 'policy', phase: 'full', status: 'failure', checks, ...extra });
const failed = (name, details) => [{ name: 'changed files', status: 'success', details: 'ok' }, { name, status: 'failure', details }];

// ---- the mention
test('only a plain GitHub login is ever mentioned', () => {
  assert.equal(notify.mention('octocat'), '@octocat');
  assert.equal(notify.mention('a-b-c'), '@a-b-c');
  for (const bad of ['', null, undefined, 42, 'dependabot[bot]', 'a b', 'x\n@y', '@octocat', '-lead', 'a'.repeat(40), 'org/team', 'ok<script>', 'a..b'])
    assert.equal(notify.mention(bad), null, JSON.stringify(bad));
});

// ---- neutralising the verifier's text
test('text from the artifact is bounded, plain and cannot mention, break the fence or hide characters', () => {
  const hostile = '@everyone @org/team and @octocat\n```\n### fake heading\n```\n#123 <img src=x onerror=alert(1)> [click](http://evil.example)\u202e\u2066 \u001b[31mred\u0000';
  const safe = notify.neutralize(hostile);
  assert.ok(!safe.includes('`'), 'no backtick can close the fence');
  assert.ok(!/@(?!\u200b)/.test(safe), 'every @ is followed by a zero-width space');
  assert.ok(!/[\u202a-\u202e\u2066-\u2069\u0000\u001b]/.test(safe), 'no bidi or control characters');
  assert.ok(safe.includes('#123') && safe.includes('<img'), 'the text is kept (it is shown in a code block), only made inert');
  const long = notify.neutralize('x'.repeat(5000));
  assert.ok(long.length < 1600 && long.endsWith('[shortened]'));
  const many = notify.neutralize(Array.from({ length: 100 }, (_, i) => `line ${i}`).join('\n'));
  assert.equal(many.split('\n').length, 26);
  assert.equal(notify.neutralize(null), '');
});

test('a check name from the artifact is a short plain label or "unknown"', () => {
  assert.equal(notify.cleanName('forshor'), 'forshor');
  assert.equal(notify.cleanName('changed files'), 'changed files');
  for (const bad of ['@everyone', '`x`', '<b>', 'x'.repeat(80), '', null, 7, 'a\nb']) assert.equal(notify.cleanName(bad), 'unknown', String(bad));
});

// ---- rejected, or could not complete
const VERDICT_STAGES = ['forshor', 'lexical', 'format', 'parse', 'data', 'check', 'threshold', 'widths', 'cert', 'replay', 'audit', 'mirror'];
for (const stage of VERDICT_STAGES) test(`a failed ${stage} check is a verdict on the policy`, () => {
  const c = notify.classify({ conclusion: 'failure', result: result(failed(stage, 'detail text')) });
  assert.deepEqual(c, { outcome: 'rejected', stage, reason: 'detail text' });
});

test('changed files: a forbidden extra file is a verdict, an unavailable comparison is not', () => {
  const verdict = 'only TableGeneration/Submission/Policy.lean may change; also changed: TableGeneration/Submission/Policy/X.lean';
  assert.equal(notify.classify({ conclusion: 'failure', result: result([{ name: 'changed files', status: 'failure', details: verdict }]) }).outcome, 'rejected');
  const tool = notify.classify({ conclusion: 'failure', result: result([{ name: 'changed files', status: 'failure', details: 'could not compare with the base commit' }]) });
  assert.equal(tool.outcome, 'incomplete'); assert.equal(tool.cause, 'tool');
});

for (const name of ['trees', 'verification', 'verifier exception']) test(`a failed "${name}" check is a tool or environment failure, not a verdict`, () => {
  const c = notify.classify({ conclusion: 'failure', result: result(failed(name, 'x')) });
  assert.equal(c.outcome, 'incomplete'); assert.equal(c.cause, 'tool');
});

for (const stage of ['harness', 'killed', 'epoch']) test(`a ${stage} stage is a limit or infrastructure fault, not a rejection`, () => {
  const c = notify.classify({ conclusion: 'failure', result: result(failed(stage, 'x')) });
  assert.equal(c.outcome, 'incomplete'); assert.equal(c.cause, 'limits');
});

for (const [conclusion, cause] of [['cancelled', 'cancelled'], ['timed_out', 'timed_out'], ['startup_failure', 'startup']]) test(`a ${conclusion} run could not complete`, () => {
  assert.deepEqual(notify.classify({ conclusion, result: result(failed('forshor', 'x')) }), { outcome: 'incomplete', cause });
});

test('no readable result, or a result that contradicts the failed run, is never a verdict', () => {
  for (const r of [null, undefined, 'text', {}, { status: 'success', checks: [] }, { status: 'failure', checks: 'x' }, { status: 'failure', checks: [] },
    { status: 'failure', checks: [{ name: 'a', status: 'success' }] }, { status: 'success', checks: [{ name: 'forshor', status: 'failure' }] }])
    assert.deepEqual(notify.classify({ conclusion: 'failure', result: r }), { outcome: 'incomplete', cause: 'no_result' }, JSON.stringify(r));
});

test('an unknown or hostile stage name is shown as "unknown", and a rejection is still a rejection', () => {
  const c = notify.classify({ conclusion: 'failure', result: result(failed('@everyone', 'x')) });
  assert.equal(c.outcome, 'rejected'); assert.equal(c.stage, 'unknown');
});

// ---- the comments
const body = (over = {}) => notify.buildComment({ outcome: 'rejected', login: 'contributor', headSha: HEAD, runUrl: RUN_URL, stage: 'forshor', reason: 'error: type mismatch', ...over });

test('rejected: mentions the author, names the stage, shows the reason in one fence, says what to do and links the run', () => {
  const b = body();
  assert.ok(b.startsWith(`<!-- forshor-notify:${HEAD}:rejected -->\n### Proof check failed`));
  assert.match(b, /@contributor, your policy did not pass verification for commit `aaaaaaa`/);
  assert.match(b, /\*\*What failed:\*\* ForShor's Lean build or policy check did not pass\./);
  assert.match(b, /```text\nerror: type mismatch\n```/);
  assert.match(b, /push a new commit/); assert.match(b, /`\.\/forshor check`/); assert.match(b, /`\.\/forshor status`/);
  assert.ok(b.includes(`[Verification run](${RUN_URL})`));
  assert.equal(b.split('```').length - 1, 2, 'exactly one fence');
});

test('rejected: every plain-language stage description is present', () => {
  for (const stage of VERDICT_STAGES) assert.doesNotMatch(body({ stage }), /The check named "/, `${stage} has a plain-language description`);
  assert.match(body({ stage: 'something-new' }), /The check named "something-new" did not pass\./, 'an unlisted but plain stage is shown by name');
});

test('could not complete: says it is not a verdict, blames nothing, shows tool detail when there is some, and links the run', () => {
  const b = notify.buildComment({ outcome: 'incomplete', login: 'contributor', headSha: HEAD, runUrl: RUN_URL, cause: 'tool', reason: 'the verification could not complete (a tool failed)' });
  assert.ok(b.startsWith(`<!-- forshor-notify:${HEAD}:incomplete -->\n### Verification could not complete`));
  assert.match(b, /@contributor, the automated check for commit `aaaaaaa` did not finish\. \*\*This is not a verdict on your policy\.\*\*/);
  assert.match(b, /```text\nthe verification could not complete/);
  assert.match(b, /You do not need to change anything/); assert.ok(b.includes(`(${RUN_URL})`));
  const plain = notify.buildComment({ outcome: 'incomplete', login: 'contributor', headSha: HEAD, runUrl: RUN_URL, cause: 'cancelled' });
  assert.doesNotMatch(plain, /```/); assert.match(plain, /cancelled/);
});

test('hostile reason text and hostile login cannot ping anyone else or inject markdown', () => {
  const hostile = '@everyone @org/team #123 <script>x</script> ```\n### injected\n[a](http://evil.example)\n' + 'y'.repeat(4000);
  for (const outcome of ['rejected', 'incomplete']) {
    const b = notify.buildComment({ outcome, login: 'contributor', headSha: HEAD, runUrl: RUN_URL, stage: 'forshor', reason: hostile, cause: 'tool' });
    assert.equal((b.match(/@(?!\u200b)/g) ?? []).length, 1, `${outcome}: the only live @ is the author's`);
    assert.equal(b.split('```').length - 1, 2, `${outcome}: the fence cannot be closed early`);
    assert.ok(b.length < 3500, `${outcome}: bounded`);
  }
  for (const login of ['x\n@victim', 'a b', '@victim', null]) {
    const b = notify.buildComment({ outcome: 'rejected', login, headSha: HEAD, runUrl: RUN_URL, stage: 'forshor', reason: 'x' });
    assert.equal((b.match(/@(?!\u200b)/g) ?? []).length, 0, `no mention for ${JSON.stringify(login)}`);
  }
  const named = notify.buildComment({ outcome: 'rejected', login: 'c', headSha: HEAD, runUrl: RUN_URL, stage: '@everyone', reason: 'x' });
  assert.ok(!/@everyone/.test(named));
});

// ---- idempotence
function fakeComments(initial = []) {
  const comments = [...initial]; const calls = [];
  return { comments, calls, github: {
    paginate: async (method, params) => method(params),
    rest: { issues: {
      listComments: async () => comments,
      createComment: async args => { calls.push('create'); comments.push({ id: comments.length + 1, body: args.body, user: { login: 'github-actions[bot]' } }); },
      updateComment: async args => { calls.push('update'); comments.find(c => c.id === args.comment_id).body = args.body; },
    } } } };
}
const ctx = { repo: { owner: 'zapatacomputing', repo: 'forshor-submissions' } };

test('one comment per head commit and outcome: created once, updated in place, an identical body is left alone', async () => {
  const f = fakeComments();
  const marker = notify.markerOf(HEAD, 'rejected');
  assert.equal(await notify.upsert({ github: f.github, context: ctx, prNumber: 5, marker, body: `${marker}\none` }), 'created');
  assert.equal(await notify.upsert({ github: f.github, context: ctx, prNumber: 5, marker, body: `${marker}\ntwo` }), 'updated');
  assert.equal(await notify.upsert({ github: f.github, context: ctx, prNumber: 5, marker, body: `${marker}\ntwo` }), 'updated');
  assert.deepEqual(f.calls, ['create', 'update']);
  assert.equal(f.comments.length, 1);
  const other = notify.markerOf(HEAD, 'incomplete');
  await notify.upsert({ github: f.github, context: ctx, prNumber: 5, marker: other, body: `${other}\nx` });
  assert.equal(f.comments.length, 2, 'another outcome for the same commit is its own comment (a new ping)');
});

test('a comment that only imitates the marker, from another user, is not reused', async () => {
  const marker = notify.markerOf(HEAD, 'rejected');
  const f = fakeComments([{ id: 1, body: `${marker}\nspoof`, user: { login: 'attacker' } }]);
  assert.equal(await notify.upsert({ github: f.github, context: ctx, prNumber: 5, marker, body: `${marker}\nreal` }), 'created');
  assert.equal(f.comments[0].body, `${marker}\nspoof`);
});

// ---- inspect and post, with a fake GitHub
async function inTemp(fn) {
  const root = process.cwd(), dir = fs.mkdtempSync(path.join(os.tmpdir(), 'forshor-notify-'));
  process.chdir(dir);
  try { await fn(dir); } finally { process.chdir(root); fs.rmSync(dir, { recursive: true, force: true }); }
}

function world({ conclusion = 'failure', files = ['TableGeneration/Submission/Policy.lean'], associated = true, pull = {}, runOverride = {}, action = 'completed', jobs = [] } = {}) {
  const outputs = {}, notices = [], warnings = [], infos = [];
  const run = { id: 123, run_attempt: 1, event: 'pull_request', path: SOURCE, head_sha: HEAD, status: 'completed', conclusion, ...runOverride };
  const comments = fakeComments();
  const pr = { number: 7, state: 'open', merged: false, user: { login: 'contributor' }, head: { sha: HEAD }, base: { ref: 'main', repo: { full_name: REPO } }, ...pull };
  const github = {
    paginate: async (method, params) => method(params),
    rest: {
      actions: { getWorkflowRunAttempt: async () => ({ data: run }), getWorkflowRun: async () => ({ data: run }),
        listWorkflowRuns: async () => ({ data: { workflow_runs: [run] } }),
        listJobsForWorkflowRunAttempt: async args => {
          assert.equal(args.run_id, run.id); assert.equal(args.attempt_number, run.run_attempt);
          return { data: { jobs } };
        } },
      repos: { listPullRequestsAssociatedWithCommit: async () => (associated ? [{ number: 7, base: { ref: 'main' }, head: { sha: HEAD } }] : []) },
      pulls: { listFiles: async () => files.map(filename => ({ filename })), get: async () => ({ data: pr }) },
      issues: comments.github.rest.issues,
    },
  };
  const core = { setOutput: (k, v) => { outputs[k] = v; }, notice: m => notices.push(m), warning: m => warnings.push(m), info: m => infos.push(m) };
  const context = { ...ctx, payload: { action, workflow_run: { id: 123, run_attempt: 1, event: 'pull_request', path: SOURCE, head_sha: HEAD, conclusion } } };
  return { github, core, context, outputs, notices, warnings, infos, comments, run, pr, jobs };
}

test('inspect: a failed run on a current pull request is bound to the API, and the artifact is worth fetching', () => inTemp(async () => {
  const w = world();
  await notify.inspect(w);
  assert.equal(w.outputs.process, 'true'); assert.equal(w.outputs.download, 'true');
  assert.equal(w.outputs.artifact, 'table-generation-submission-123-1');
  assert.deepEqual(JSON.parse(fs.readFileSync('notify-source.json', 'utf8')),
    { run: { id: 123, run_attempt: 1, conclusion: 'failure' }, pr: { number: 7, headSha: HEAD }, author: 'contributor', refusal: null });
}));

test('inspect: skipped and action_required are not an outcome', () => inTemp(async () => {
  for (const conclusion of ['skipped', 'action_required', 'neutral']) {
    const w = world({ conclusion }); await notify.inspect(w);
    assert.equal(w.outputs.process, 'false', conclusion); assert.ok(!fs.existsSync('notify-source.json'));
  }
}));

test('inspect: another workflow, another event or a changed run identity is refused', () => inTemp(async () => {
  for (const patch of [{ path: '.github/workflows/other.yml' }, { event: 'push' }, { head_sha: 'nope' }, { id: 1.5 }]) {
    const w = world(); Object.assign(w.context.payload.workflow_run, patch);
    await assert.rejects(notify.inspect(w), /Unexpected source verification workflow/);
  }
  const w = world({ runOverride: { head_sha: OTHER } });
  await assert.rejects(notify.inspect(w), /Source workflow identity changed/);
}));

test('inspect: no current pull request, a closed one or a moved head means nobody is told', () => inTemp(async () => {
  for (const options of [{ associated: false }, { pull: { state: 'closed' } }, { pull: { merged: true } }, { pull: { head: { sha: OTHER } } },
    { pull: { base: { ref: 'main', repo: { full_name: 'someone/fork' } } } }]) {
    const w = world(options); await notify.inspect(w);
    assert.equal(w.outputs.process, 'false', JSON.stringify(options)); assert.ok(!fs.existsSync('notify-source.json'));
  }
}));

test('inspect: an API failure while resolving the pull request fails the job instead of staying silent', () => inTemp(async () => {
  const w = world();
  w.github.rest.pulls.listFiles = async () => { throw new Error('GitHub API unavailable'); };
  await assert.rejects(notify.inspect(w), /GitHub API unavailable/);
  assert.ok(!fs.existsSync('notify-source.json'));
}));

test('inspect: a pull request that changes files outside the submission surface is rejected from the API alone, without the artifact', () => inTemp(async () => {
  const w = world({ files: ['TableGeneration/Submission/Policy.lean', '.github/workflows/table-generation-submission.yml'] });
  await notify.inspect(w);
  assert.equal(w.outputs.process, 'true'); assert.equal(w.outputs.download, 'false');
  const source = JSON.parse(fs.readFileSync('notify-source.json', 'utf8'));
  assert.match(source.refusal, /outside the submission surface/); assert.equal(source.pr.number, 7);
  await notify.post(w);
  assert.equal(w.comments.comments.length, 1);
  assert.match(w.comments.comments[0].body, /### Submission not accepted/); assert.match(w.comments.comments[0].body, /@contributor, your policy/);
}));

test('post: a rejected policy is reported once, in place on a re-run, from the artifact as data', () => inTemp(async () => {
  const w = world();
  await notify.inspect(w);
  fs.mkdirSync('artifacts');
  fs.writeFileSync('artifacts/table-generation-submission-result.json', JSON.stringify(result(failed('forshor', "ForShor's build of the policy template failed: error: x"))));
  await notify.post(w); await notify.post(w);
  assert.deepEqual(w.comments.calls, ['create']);
  const b = w.comments.comments[0].body;
  assert.match(b, /### Proof check failed/); assert.match(b, /@contributor, your policy did not pass/); assert.match(b, /ForShor's build of the policy template failed/);
  assert.ok(b.includes(RUN_URL));
}));

test('post: a missing, unreadable, oversized or contradictory result is "could not complete", never a verdict', () => inTemp(async () => {
  for (const write of [() => {}, () => fs.writeFileSync('artifacts/table-generation-submission-result.json', '{not json'),
    () => fs.writeFileSync('artifacts/table-generation-submission-result.json', ' '.repeat(1100000)),
    () => fs.writeFileSync('artifacts/table-generation-submission-result.json', JSON.stringify({ status: 'success', checks: [] }))]) {
    fs.rmSync('artifacts', { recursive: true, force: true }); fs.mkdirSync('artifacts'); write();
    const w = world(); await notify.inspect(w); await notify.post(w);
    assert.match(w.comments.comments[0].body, /### Verification could not complete/);
    assert.match(w.comments.comments[0].body, /not a verdict on your policy/);
  }
}));

test('post: a cancelled or timed-out run says so, and never blames the policy', () => inTemp(async () => {
  for (const [conclusion, text] of [['cancelled', /cancelled/], ['timed_out', /ran out of time/]]) {
    const w = world({ conclusion }); await notify.inspect(w); await notify.post(w);
    assert.match(w.comments.comments[0].body, text); assert.doesNotMatch(w.comments.comments[0].body, /did not pass verification/);
  }
}));

test('post: no author (a deleted account) still posts, without a mention', () => inTemp(async () => {
  const w = world({ pull: { user: null } }); await notify.inspect(w);
  fs.mkdirSync('artifacts');
  fs.writeFileSync('artifacts/table-generation-submission-result.json', JSON.stringify(result(failed('cert', 'x'))));
  await notify.post(w);
  assert.ok(!/@(?!\u200b)/.test(w.comments.comments[0].body));
}));

test('the dry-run command prints the comment and posts nothing', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'forshor-notify-dry-'));
  fs.writeFileSync(path.join(dir, 'table-generation-submission-result.json'), JSON.stringify(result(failed('cert', 'the kernel said no'))));
  const out = require('node:child_process').execFileSync('node', [path.join(__dirname, 'ci/notify_submitters.js'), '--dry-run', `--dir=${dir}`, '--login=contributor', `--sha=${HEAD}`, '--run-id=123'], { encoding: 'utf8' });
  assert.match(out, /### Proof check failed/); assert.match(out, /the kernel said no/);
  assert.throws(() => require('node:child_process').execFileSync('node', [path.join(__dirname, 'ci/notify_submitters.js')], { stdio: 'pipe' }));
  fs.rmSync(dir, { recursive: true, force: true });
});

// ---- the workflow's trust boundary
const workflow = fs.readFileSync(path.join(__dirname, '..', '.github/workflows/notify-submitters.yml'), 'utf8');
const stripComments = text => text.split('\n').filter(l => !l.trim().startsWith('#')).join('\n');
const code = stripComments(workflow);

test('the workflow can only comment: no secret, no identity token, no write to contents or checks, read-only elsewhere', () => {
  assert.match(code, /^permissions: \{\}$/m, 'nothing by default');
  const job = code.slice(code.indexOf('    permissions:\n'), code.indexOf('    steps:'));
  const granted = [...job.matchAll(/^      ([a-z-]+): (read|write)$/gm)].map(m => `${m[1]}:${m[2]}`).sort();
  assert.deepEqual(granted, ['actions:read', 'contents:read', 'pull-requests:write']);
  assert.doesNotMatch(code, /secrets\./); assert.doesNotMatch(code, /id-token/); assert.doesNotMatch(code, /contents: write|checks: write|issues: write/);
  assert.doesNotMatch(code, /google-github-actions|PUBLISHER_/);
});

test('the workflow runs trusted main, never the submission, and treats the artifact as data', () => {
  const checkouts = [...code.matchAll(/uses: actions\/checkout@[0-9a-f]{40}\n        with:\n([\s\S]*?)(?=\n      - name:|\n$)/g)];
  assert.equal(checkouts.length, 1, 'one checkout');
  assert.match(checkouts[0][1], /ref: main/); assert.match(checkouts[0][1], /persist-credentials: false/);
  assert.doesNotMatch(checkouts[0][1], /workflow_run\.head_(sha|branch|repository)|pull_request\.head|github\.head_ref|refs\/pull/);
  assert.match(code, /name: \$\{\{ steps\.source\.outputs\.artifact \}\}/); assert.match(code, /run-id: \$\{\{ github\.event\.workflow_run\.id \}\}/);
  for (const step of code.split('\n      - name: ').slice(1)) {
    if (/^\s*run:/m.test(step) || /\n        run:/.test(step)) assert.doesNotMatch(step, /\$\{\{\s*github\.event\./, 'no event field is interpolated into a shell command');
  }
  assert.ok(code.indexOf('scripts/test_notify_submitters.js') < code.indexOf('.inspect('), 'the tests run before anything is posted');
  assert.doesNotMatch(code, /(?:python3?|node|sh|bash) +artifacts\//, 'nothing from the artifact is executed');
});

test('the workflow reports progress and completed pull-request verifications of the policy submission workflow', () => {
  assert.match(code, /workflows: \[Policy submission\]/); assert.match(code, /types: \[requested, in_progress, completed\]/);
  assert.match(code, /workflow_run\.event == 'pull_request'/);
  assert.match(code, /github\.event\.action != 'completed'/);
  assert.match(code, /\["success","failure","timed_out","cancelled","startup_failure"\]/);
  assert.match(code, /group: forshor-notify-\$\{\{ github\.event\.workflow_run\.head_sha \}\}/);
  assert.match(code, /cancel-in-progress: false/);
});

test('the publisher still runs this module\'s tests and keeps its own boundaries', () => {
  const publish = fs.readFileSync(path.join(__dirname, '..', '.github/workflows/publish-results.yml'), 'utf8');
  assert.match(publish, /node --test scripts\/test_notify_submitters\.js/);
  assert.doesNotMatch(publish, /pull_request_target/);
});

// ---- the optional summary.tex: its rules are named in plain words, and a summary-only pull request is told why
test('classify: a broken summary rule is a rejection of the file, named "summary"; a changed-files verdict about the pair is a verdict', () => {
  const rule = 'summary.tex is 120,000 bytes; the limit is 102,400 bytes (100 KiB).';
  assert.deepEqual(notify.classify({ conclusion: 'failure', result: result(failed('summary', rule)) }), { outcome: 'rejected', stage: 'summary', reason: rule });
  for (const details of ['only TableGeneration/Submission/Policy.lean (and the optional TableGeneration/Submission/summary.tex) may change; also changed: README.md',
                         'a submission needs a policy: TableGeneration/Submission/summary.tex changed but TableGeneration/Submission/Policy.lean did not.']) {
    assert.equal(notify.classify({ conclusion: 'failure', result: result([{ name: 'changed files', status: 'failure', details }]) }).outcome, 'rejected', details);
  }
});

test('buildComment: a summary-rule rejection says it is about the file, not the policy, and shows the rule', () => {
  const body = notify.buildComment({ outcome: 'rejected', login: 'contributor', headSha: HEAD, runUrl: RUN_URL, stage: 'summary',
    reason: 'summary.tex contains the control character U+0000 (character 5); only tab, line feed and carriage return are allowed.' });
  assert.match(body, /### Submission not accepted/);
  assert.match(body, /@contributor, commit `a{7}` was not accepted because its optional summary file breaks a rule\. Your policy itself was not judged/);
  assert.match(body, /summary\.tex contains the control character U\+0000/);
  assert.match(body, /never compiled or run/);
  assert.doesNotMatch(body, /your policy did not pass verification/);
});

test('buildComment: hostile summary text in a rule message cannot mention, break the fence or inject', () => {
  const body = notify.buildComment({ outcome: 'rejected', login: 'contributor', headSha: HEAD, runUrl: RUN_URL, stage: 'summary',
    reason: '```\n@everyone <script>alert(1)</script> \\write18{rm -rf /}\n```' });
  assert.ok(!body.includes('@everyone'), 'mentions are neutralised');
  assert.equal(body.split('```').length - 1, 2, 'the fence the comment opens is the only fence');
});

test('inspect: a summary-only pull request is refused from the API alone, in plain words', () => inTemp(async () => {
  const w = world({ files: ['TableGeneration/Submission/summary.tex'] });
  await notify.inspect(w);
  assert.equal(w.outputs.process, 'true'); assert.equal(w.outputs.download, 'false');
  const source = JSON.parse(fs.readFileSync('notify-source.json', 'utf8'));
  assert.match(source.refusal, /summary\.tex alone is not a submission/);
  await notify.post(w);
  assert.match(w.comments.comments[0].body, /### Submission not accepted/);
}));

test('inspect: a policy with a notes.tex beside it is refused, naming the allowed pair', () => inTemp(async () => {
  const w = world({ files: ['TableGeneration/Submission/Policy.lean', 'TableGeneration/Submission/summary.tex', 'TableGeneration/Submission/notes.tex'] });
  await notify.inspect(w);
  const source = JSON.parse(fs.readFileSync('notify-source.json', 'utf8'));
  assert.match(source.refusal, /notes\.tex/); assert.match(source.refusal, /Policy\.lean and the optional .*summary\.tex/);
}));

test('inspect: a policy with its summary.tex is an ordinary submission pull request', () => inTemp(async () => {
  const w = world({ files: ['TableGeneration/Submission/Policy.lean', 'TableGeneration/Submission/summary.tex'] });
  await notify.inspect(w);
  assert.equal(w.outputs.process, 'true'); assert.equal(w.outputs.download, 'true');
  assert.equal(JSON.parse(fs.readFileSync('notify-source.json', 'utf8')).refusal, null);
}));

test('memory and time guard stops, including old replay/cert diagnostics, never say to fix a proof', () => {
  for (const [stage, details, cause] of [
    ['memory', 'killed: memory 12.04 GB > cap 12.0 GB', 'memory'],
    ['replay', 'kernel replay failed: killed: memory 10.14 GB > cap 10.0 GB', 'memory'],
    ['cert', 'killed: memory 12.04 GiB > cap 12.0 GiB', 'memory'],
    ['timeout', 'the stage exceeded its deadline', 'timed_out'],
    ['replay', 'killed: timeout 900 s', 'timed_out'],
  ]) {
    const verdict = notify.classify({ conclusion: 'failure', result: result(failed(stage, details)) });
    assert.equal(verdict.outcome, 'incomplete'); assert.equal(verdict.cause, cause);
    const b = body(verdict);
    assert.match(b, /not a verdict on your policy/);
    assert.doesNotMatch(b, /Proof check failed|Submission not accepted|Fix the problem/);
    if (cause === 'memory') {
      assert.match(b, /### Stopped: memory limit reached/);
      assert.match(b, /We're working towards supporting submissions that need more memory/);
      assert.match(b, /maintainer can retry/);
    } else assert.match(b, /### Stopped: time limit reached/);
  }
});

test('queued, verifying and verified update one comment; progress uses job status, never an artifact', () => inTemp(async () => {
  const w = world({ conclusion: null, action: 'requested', runOverride: { status: 'queued' } });
  fs.mkdirSync('artifacts');
  fs.writeFileSync('artifacts/table-generation-submission-result.json', JSON.stringify(result(failed('forshor', 'unrelated proof failure'))));
  await notify.inspect(w); await notify.post(w);
  assert.equal(w.outputs.download, 'false');
  assert.match(w.comments.comments[0].body, /### Queued — waiting for verification/);
  // A workflow marked in_progress can still have its verification job queued.
  w.context.payload.action = 'in_progress'; w.run.status = 'in_progress';
  w.jobs.push({ name: 'Verify submission with Lean', status: 'queued' });
  await notify.inspect(w); await notify.post(w);
  assert.match(w.comments.comments[0].body, /### Queued/);
  w.jobs[0].status = 'in_progress';
  await notify.inspect(w); await notify.post(w);
  assert.match(w.comments.comments[0].body, /### Verifying/);
  w.run.status = 'completed'; w.run.conclusion = 'success';
  w.context.payload.action = 'completed'; w.context.payload.workflow_run.conclusion = 'success';
  await notify.inspect(w); await notify.post(w);
  assert.equal(w.outputs.download, 'false');
  assert.equal(w.comments.comments.length, 1);
  const b = w.comments.comments[0].body;
  assert.match(b, /### Verification passed/);
  assert.match(b, /check on this PR for publication status/);
  assert.doesNotMatch(b, /unrelated proof failure|### Submission accepted/);
  assert.ok(b.startsWith(notify.statusMarkerOf(HEAD)));
}));

test('a queued comment becomes a memory stop in the same comment', () => inTemp(async () => {
  const w = world({ conclusion: null, action: 'requested', runOverride: { status: 'queued' } });
  await notify.inspect(w); await notify.post(w);
  w.run.status = 'completed'; w.run.conclusion = 'failure';
  w.context.payload.action = 'completed'; w.context.payload.workflow_run.conclusion = 'failure';
  fs.mkdirSync('artifacts');
  fs.writeFileSync('artifacts/table-generation-submission-result.json', JSON.stringify(result(failed('memory', 'killed: memory 12.04 GB > cap 12.0 GB'))));
  await notify.inspect(w); await notify.post(w);
  assert.equal(w.comments.comments.length, 1);
  assert.match(w.comments.comments[0].body, /### Stopped: memory limit reached/);
  assert.match(w.comments.comments[0].body, /12.04 GB/);
}));

test('delayed events use current progress and never overwrite a completed status with queued', () => inTemp(async () => {
  const w = world({ conclusion: null, action: 'requested', runOverride: { status: 'queued' } });
  await notify.inspect(w);
  w.run.status = 'in_progress'; w.jobs.push({ name: 'Verify submission with Lean', status: 'in_progress' });
  await notify.post(w);
  assert.match(w.comments.comments[0].body, /### Verifying/);
  w.run.status = 'completed'; w.run.conclusion = 'success';
  await notify.post(w);
  assert.equal(w.comments.calls.length, 1, 'an already inspected progress event cannot overwrite a terminal result');
  await notify.inspect(w); await notify.post(w);
  assert.match(w.comments.comments[0].body, /### Verification passed/, 'an old event inspected late reads current state');
}));

test('post rechecks attempt, PR head and workflow identity after inspection', () => inTemp(async () => {
  for (const alter of [w => w.run.run_attempt++, w => { w.pr.head.sha = OTHER; }, w => { w.pr.state = 'closed'; },
    w => { w.run.path = '.github/workflows/other.yml'; }, w => { w.run.event = 'push'; }]) {
    const w = world(); await notify.inspect(w); alter(w); await notify.post(w);
    assert.equal(w.comments.calls.length, 0);
  }
}));

test('an older run for the same commit cannot overwrite the newer run status', () => inTemp(async () => {
  const w = world(); await notify.inspect(w);
  w.github.rest.actions.listWorkflowRuns = async () => ({ data: { workflow_runs: [{ ...w.run, id: 124 }] } });
  await notify.post(w);
  assert.equal(w.comments.calls.length, 0);
}));

test('verified status can finish an existing comment after publication closes the PR, but never creates one on a closed PR', () => inTemp(async () => {
  const w = world({ conclusion: 'success' });
  await notify.inspect(w); await notify.post(w);
  const publication = { id: 99, body: `<!-- forshor-publication:${HEAD} -->\nPublished`, user: { login: 'github-actions[bot]' } };
  w.comments.comments.push(publication);
  w.comments.comments[0].body = `${notify.statusMarkerOf(HEAD)}\nQueued`;
  w.pr.state = 'closed';
  await notify.inspect(w); await notify.post(w);
  assert.match(w.comments.comments[0].body, /### Verification passed/);
  assert.equal(w.comments.comments[1], publication, 'publisher comment is independent');
  const closed = world({ conclusion: 'success', pull: { state: 'closed' } });
  await notify.inspect(closed); await notify.post(closed);
  assert.equal(closed.comments.calls.length, 0);
}));
