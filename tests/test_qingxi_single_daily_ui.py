"""Actual UI rendering/action code under a minimal offline DOM event boundary."""
import json
from pathlib import Path
import subprocess


def test_single_slot_buttons_and_scoped_review_use_exact_api_fields(tmp_path):
    source = (Path(__file__).parents[1] / 'model/web/static/js/dungeon_ui.js').read_text()
    script = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');const listeners=[],calls=[];
const c={console,document:{getElementById:()=>null,addEventListener:(kind,fn)=>{if(kind==='click')listeners.push(fn)}},
 escapeHtml:value=>String(value??''),postJson:async(url,payload)=>{calls.push({url,payload});return {}},
 updateFlash:()=>{},refreshState:async()=>{},registerRenderHook:()=>{}};
c.window=c;vm.createContext(c);vm.runInContext(SOURCE,c);
const ids=['moonpalace','bloodland','southgate','escort','mulanshrine','bqpuppet','jianzhong'];
const progress={phase:'idle',claimed:0,trial_used:0,slots:ids.map(id=>({id,title:id,state:'pending'}))};
const config={qingxi_rewards:{enabled:false,participant_identity_ids:[42]},qingxi_reward_progress:{42:progress}};
const html=c.renderQingxiRewardConfig(config,[{identity_id:42,display_name:'fixture'}],()=>'<p>members</p>');
assert.equal((html.match(/>单独执行<\/button>/g)||[]).length,7);
assert.ok(html.includes('data-qingxi-reward-action="rewards_refresh"'));
progress.single_daily_id='moonpalace';progress.phase='held';
const held=c.renderQingxiRewardConfig(config,[{identity_id:42,display_name:'fixture'}],()=>'<p>members</p>');
assert.ok(held.includes('data-qingxi-reward-action="reward_one_refresh" data-qingxi-reward-daily="moonpalace"'));
assert.equal((held.match(/disabled>单独执行/g)||[]).length,7);
function event(action,daily){const button={disabled:false,dataset:{qingxiRewardAction:action,qingxiRewardIdentity:'42',
 ...(daily?{qingxiRewardDaily:daily}:{})}};return {target:{getAttribute:()=>null,
 closest:selector=>selector==='[data-qingxi-reward-action]'?button:null}}}
(async()=>{
 for(const fn of listeners)await fn(event('reward_one','jianzhong'));
 for(const fn of listeners)await fn(event('reward_one_refresh','moonpalace'));
 for(const fn of listeners)await fn(event('rewards'));
 assert.equal(calls.length,3);
 assert.equal(calls[0].payload.daily_id,'jianzhong');assert.equal(calls[0].payload.action,'reward_one');
 assert.equal(calls[1].payload.daily_id,'moonpalace');assert.equal(calls[1].payload.action,'reward_one_refresh');
 assert.ok(!Object.hasOwn(calls[2].payload,'daily_id'));assert.equal(calls[2].payload.action,'rewards');
 assert.ok(calls.every(call=>call.url==='/api/qingxi-quests'&&call.payload.send_as_id==='42'));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    path = tmp_path / 'single-ui.cjs'
    path.write_text('const SOURCE=' + json.dumps(source) + ';' + script)
    subprocess.run(['node', str(path)], check=True, capture_output=True, text=True, timeout=10)
