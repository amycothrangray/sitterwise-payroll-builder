// Run with Node. Exercise rendered handlers after browser-style entity decoding.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
const handlers = {};
let request;
const context = vm.createContext({
  location: { hash: '#/home', href: 'http://127.0.0.1:8756/', origin:'http://127.0.0.1:8756' },
  sessionStorage: { getItem: () => 'test-key' },
  document: { addEventListener: (name, fn) => handlers[name] = fn },
  window: { addEventListener: () => {} },
  URL, Headers,
  fetch: async (url, options) => { request = {url, options}; return {ok:true}; },
  setTimeout: () => {},
});
// The only startup call renders asynchronously; suppress it in the isolated harness.
vm.runInContext(source.replace(/^route\(\);$/m, ''), context);
const reminders = vm.runInContext(`weeklyChecklist({
  checklist:[{key:"recurring:test",title:"Verify Test Administrator: $150.00 in OnPay",
    detail:"Check after importing.",available:true,checked:false}],
  waiting_notes:[{caregiver_name:"<img src=x>",kind_label:"Extra pay",amount:"25.00",detail:"Training"}],
  applied_notes:[]
})`, context);
assert.ok(reminders.includes('This week’s reminders'));
assert.ok(reminders.includes('Odds &amp; Ends') || reminders.includes('Odds & Ends'));
assert.ok(reminders.includes('$150.00'));
assert.ok(!reminders.includes('<img'));
assert.ok(source.includes('Copy task for Claude Cowork'));
assert.ok(!source.includes('ChatGPT Work'));
assert.ok(source.includes('accept=".pdf,application/pdf"'));
// The caregiver handoff stays in step 3 with CalSavers immediately next.
const flow = vm.runInContext(`
  S.runId='abc'; S.downloads=[{key:'onpay_import',download_blocked:false,
    summary:{people:1,hours:'4',total:'92'}}];
  S.run={run:{locked:false,label:'Test week'},findings:[],caregivers:[],
    totals:{caregivers:1,jobs:1,total_paid:'92'},summary:{can_finalize:true},checklist:[]};
  VIEWS.payroll();`, context);
assert.ok(flow.includes('Download caregiver PDFs'));
assert.ok(flow.includes('/api/runs/abc/export/caregiver_pdfs'));
assert.ok(flow.includes('Menu &gt; My Files'));
assert.ok(flow.indexOf('Share caregiver breakdowns') < flow.indexOf('CalSavers contributions'));
const blockedFlow=vm.runInContext(`S.downloads[0].download_blocked=true; VIEWS.payroll();`,context);
assert.ok(!blockedFlow.includes('/export/caregiver_pdfs'));
const decode = s => s.replace(/&(quot|#39|lt|gt|amp);/g, (_, k) =>
  ({quot:'"','#39':"'",lt:'<',gt:'>',amp:'&'}[k]));
for (const value of ["O'Connor", "');globalThis.compromised=true;//", '&quot;);globalThis.compromised=true;//', '<img src=x onerror=alert(1)>', 'a\\b\n"']) {
  context.sample = value;
  const html = vm.runInContext('findingCard({level:"review",caregiver_key:sample,detail:sample,title:sample,booking_ids:[]})', context);
  const attr = html.match(/onclick="([^"]*)"/)[1];
  context.openFinding = v => { context.received = v; };
  vm.runInContext(decode(attr), context);
  assert.equal(context.received,value);
  assert.equal(context.compromised,undefined);
  assert.ok(!html.includes('<img'));
}
(async () => {
  await vm.runInContext('authenticatedFetch("/api/upload", {method:"POST", headers:{"X-Filename":"test.csv"},body:"test"})',context);
  assert.equal(request.options.headers.get('Authorization'),'Bearer test-key');
  assert.equal(request.options.headers.get('X-Filename'),'test.csv');
  assert.equal(request.options.redirect,'error');
  await assert.rejects(vm.runInContext('authenticatedFetch("https://attacker.example/api/state")', context));
  // A canceled historical download must not start an authenticated fetch.
  request = null;
  await handlers.click({defaultPrevented:true,target:{closest:()=>({href:'http://127.0.0.1:8756/api/history-transfer'})}});
  assert.equal(request,null);
  console.log('Web security checks passed: imported text, API authorization, destination checks, canceled downloads.');
})().catch(e=>{ console.error(e); process.exitCode=1; });
