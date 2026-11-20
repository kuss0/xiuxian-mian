"""Use the real Mini App, original rendering and browser-native human-paced inputs."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import signal
import time
from urllib.parse import parse_qsl,urlsplit,urlunsplit

from playwright.async_api import async_playwright
from fanren_browser_support.asset_cache import VerifiedAssetCache
from fanren_browser_support.human_input import HumanInput
from fanren_browser_support.scenarios import NativeScenarios
from fanren_browser_support.native_driver import NativeInputDriver

ORIGIN='https://asc.aiopenai.app'
SUPPORT=Path(__file__).with_name('fanren_browser_support')


def normalize_launch_url(value):
    parsed=urlsplit(value.strip())
    params=dict(parse_qsl(parsed.query)+parse_qsl(parsed.fragment))
    if (parsed.scheme!='https' or parsed.hostname!='asc.aiopenai.app' or parsed.port not in (None,443)
            or parsed.username or parsed.password or parsed.path not in {'/miniapp/xianxia-dwelling','/miniapp/xianxia-verify'}
            or not params.get('tgWebAppData')):
        raise RuntimeError('invalid_launch_origin')
    return urlunsplit((parsed.scheme,parsed.netloc,'/miniapp/xianxia-dwelling',parsed.query,parsed.fragment))


def save(path,value):
    temporary=path.with_suffix('.tmp')
    with os.fdopen(os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as stream:
        json.dump(value,stream,ensure_ascii=False)
    temporary.replace(path)


def request_fact(url,body,player):
    parsed=urlsplit(url)
    if parsed.scheme+'://'+parsed.netloc!=ORIGIN or not parsed.path.startswith('/api/miniapp/xianxia-dwelling/'):
        return None
    if not isinstance(body,dict):return None
    path=parsed.path;action=body.get('action') or path.rsplit('/',1)[-1]
    family='fishing' if '/fishing/' in path else 'combat' if path.endswith('/trial') else 'npc' if path.endswith('/quests') else ''
    mutating=(family=='fishing' and action in {'cast','hook','checkpoint','fight'}
              or family=='combat' and action in {'start','checkpoint','end','retry'}
              or family=='npc' and action not in {'status','view','map'})
    if not mutating:return None
    actor=body.get('playerId')
    try:actor=int(actor)
    except (TypeError,ValueError):actor=0
    operation=str(body.get('operationId') or '')
    if not operation and action=='checkpoint':operation=str(body.get('sessionId') or body.get('runId') or '')+':'+str((body.get('fishingProof') or {}).get('durationMs') or body.get('wave') or '')
    return {'key':f'{family}:{action}:{operation}','player_id':actor,'family':family,'action':action,
            'operation_id':operation,'session_id':body.get('sessionId'),'run_id':body.get('runId'),
            'identity_matches':actor==player}


def response_fact(intent,data,status):
    row={'key':intent['key'],'player_id':intent['player_id'],'ok':False,'http_status':status,
         'family':intent['family'],'action':intent['action']}
    if not isinstance(data,dict) or not 200<=status<300 or data.get('ok') is not True:return row
    for scope in [data,data.get('account') or {},data.get('identity') or {}]:
        for key in ['playerId','selectedPlayerId']:
            if key in scope and str(scope[key])!=str(intent['player_id']):return row
    row['ok']=True
    if intent['family']=='fishing':
        session=data.get('session') or {}
        row.update(session_id=session.get('sessionId'),terminal=(session.get('result') or {}).get('ready') is True)
        if row['terminal']:row['result']=session['result']
    elif intent['family']=='combat':
        trial=data.get('trial') or {};run=trial.get('run') or {}
        row.update(run_id=run.get('id'),terminal=bool((trial.get('result') or {}).get('grade')) and not trial.get('pending') and not trial.get('anomaly'))
        if row['terminal']:row.update(result=trial['result'],grants=trial.get('grants') or [],cultivation=trial.get('cultivation') or 0)
    else:row['terminal']=True
    return row


def server_terminal(intents,receipts,scenario):
    if not intents:return False
    successful=[r for r in receipts if r.get('ok') is True]
    if scenario=='fishing':
        sessions={r.get('session_id') for r in successful if r.get('action')=='cast' and r.get('session_id')}
        return len(sessions)==1 and any(r.get('terminal') and r.get('session_id') in sessions for r in successful)
    if scenario=='combat':
        runs={r.get('run_id') for r in successful if r.get('action')=='start' and r.get('run_id')}
        return len(runs)==1 and any(r.get('terminal') and r.get('run_id') in runs for r in successful)
    return scenario=='npc' and all(r.get('family')=='npc' for r in successful)


def init_script(player,scenario):
    # The game generates all operations/proofs. This wrapper gates and journals them before transport.
    return r'''(() => {
      const expected=__PLAYER__,scenario=__SCENARIO__;
      window.__fanrenOwner=false;
      for(const [name,slot] of [['createDwelling3D','__fanrenRuntime'],['createDwellingFieldCombat','__fanrenCombat']]){
        let current;
        Object.defineProperty(window,name,{configurable:true,get:()=>current,set:value=>{
          current=typeof value==='function'?new Proxy(value,{apply(target,self,args){const result=Reflect.apply(target,self,args);
            window[slot]=result;if(slot==='__fanrenRuntime')args[0].dataset.fanrenNative='scene';return result;}}):value;
        }});
      }
      const original=window.fetch;
      window.fetch=async function(input,options){
        const req=new Request(input,options),url=new URL(req.url);
        let body={};if(req.method==='POST'){try{body=await req.clone().json();}catch{}}
        const relevant=url.origin==='https://asc.aiopenai.app'&&url.pathname.startsWith('/api/miniapp/xianxia-dwelling/');
        if(relevant){
          const tail=url.pathname.split('/').pop(),action=body.action||tail;
          const family=url.pathname.includes('/fishing/')?'fishing':tail==='trial'?'combat':tail==='quests'?'npc':'';
          const mutation=family==='fishing'&&['cast','hook','checkpoint','fight','buy-bait','chum'].includes(action)
            ||family==='combat'&&['start','checkpoint','end','retry'].includes(action)
            ||family==='npc'&&!['status','view','map'].includes(action);
          if(body.playerId!==undefined&&Number(body.playerId)!==expected){window.__fanrenOwner=false;throw Error('native_identity_mismatch');}
          if(mutation){
            if(!window.__fanrenOwner||family!==scenario||['buy-bait','chum'].includes(action))throw Error('native_operation_not_authorized');
            await window.__fanrenBeforeWrite({url:req.url,body});
          }
        }
        const response=await original.call(this,req);
        if(relevant&&url.pathname.endsWith('/start')){
          try{const data=await response.clone().json();
            if(data.ok===true&&data.account?.playerId!==undefined)window.__fanrenOwner=Number(data.account.playerId)===expected;
          }catch{}
        }
        return response;
      };
    })();'''.replace('__PLAYER__',str(player)).replace('__SCENARIO__',json.dumps(scenario))


async def run(args):
    os.umask(0o077)
    value={'identity_id':args.expected_player_id,'scenario':args.scenario,'identity_verified':False,'intents':[],
           'receipts':[],'pending':[],'unknown':False,'browser_closed':False,'server_terminal':False,'input_mode':'native mouse and keyboard'}
    args.result.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    task=asyncio.current_task()
    def cancel():stop.set();task.cancel()
    loop.add_signal_handler(signal.SIGTERM,cancel)
    pending=set();tasks=set();scenarios=None
    def persist():
        value['pending']=sorted(pending);save(args.result,value)
    async def before_write(_source,request):
        fact=request_fact(request.get('url',''),request.get('body'),args.expected_player_id)
        if fact is None or not fact['identity_matches'] or not fact['operation_id']:raise RuntimeError('native_intent_invalid')
        if stop.is_set() or fact['key'] in pending:raise RuntimeError('native_operation_pending')
        if not any(row['key']==fact['key'] for row in value['intents']):value['intents'].append(fact)
        pending.add(fact['key']);persist()
    async def on_response(response):
        try:
            request=response.request
            parsed=urlsplit(request.url)
            if parsed.scheme+'://'+parsed.netloc!=ORIGIN or not parsed.path.startswith('/api/miniapp/xianxia-dwelling/'):
                return
            body=request.post_data_json if request.post_data else {}
            fact=request_fact(request.url,body,args.expected_player_id)
            if fact is None:return
            try:data=await response.json()
            except Exception:data=None
            receipt=response_fact(fact,data,response.status)
            value['receipts'].append(receipt)
            if receipt['ok']:pending.discard(fact['key'])
            else:value['unknown']=True
            persist()
        except Exception:
            if value['intents']:value['unknown']=True
            persist()
    persist()
    try:url=normalize_launch_url(args.url_file.read_text())
    except Exception:
        value.update(error_type='InvalidLaunchURL',browser_closed=True,stage='before_browser_launch')
        persist();return
    cache_path=Path(os.environ.get('FANREN_ASSET_BUNDLE','/root/fanren-research-20261120/analysis/local-3d/bundle'))
    cache=VerifiedAssetCache(cache_path) if (cache_path/'verified-assets.json').is_file() else None
    persist()
    try:
        async with async_playwright() as p:
            context=await p.chromium.launch_persistent_context(str(args.profile_dir),executable_path='/usr/bin/chromium',headless=False,
                viewport={'width':960,'height':640},device_scale_factor=1,
                args=['--no-sandbox','--use-gl=angle','--use-angle=gl-egl','--ignore-gpu-blocklist','--disable-dev-shm-usage',
                      '--disable-background-timer-throttling','--disable-renderer-backgrounding'])
            try:
                await context.expose_binding('__fanrenBeforeWrite',before_write)
                await context.add_init_script(script=init_script(args.expected_player_id,args.scenario))
                await context.add_init_script(path=str(SUPPORT/'control_bridge.js'))
                if cache:await cache.install(context)
                page=await context.new_page()
                def response_listener(response):
                    task=asyncio.create_task(on_response(response));tasks.add(task);task.add_done_callback(tasks.discard)
                page.on('response',response_listener)
                value['stage']='loading_page';persist()
                await page.goto(url,wait_until='domcontentloaded',timeout=90000)
                if cache:await cache.refresh_grant(page)
                await page.wait_for_function('() => window.__fanrenOwner===true',timeout=90000)
                value['identity_verified']=True;persist()
                human=HumanInput(page)
                value['stage']='entering_scene';persist()
                await human.click(page.locator('#dwellingEnter'))
                await page.wait_for_function('() => window.__fanrenRuntime?.stats().companion?.modelReady',timeout=600000)
                await page.wait_for_function('() => window.__fanrenRuntime.stats().draws>2',timeout=600000)
                await page.evaluate('''() => {window.fanrenNative=window.createFanren3DBridge({runtime:window.__fanrenRuntime,ownerCheck:()=>window.__fanrenOwner===true});}''')
                buttons=await page.locator('button:visible').evaluate_all('items=>items.map(e=>({id:e.id,text:e.textContent.trim().slice(0,70)}))')
                value['visible_buttons']=buttons
                await page.screenshot(path=str(args.result.with_suffix('.png')),timeout=30000)
                scenarios=NativeScenarios(page)
                value['stage']='scenario';persist()
                if args.scenario in {'npc','combat'}:
                    town=page.locator('#worldTownEntry')
                    if await town.is_visible() and (await town.inner_text()).strip()=='前往青溪坊':
                        await human.click(town)
                        await page.wait_for_function('() => window.__fanrenRuntime.stats().environment?.tier===4 && window.__fanrenRuntime.stats().companion?.modelReady',timeout=600000)
                if args.scenario=='inspect':value['scenario_result']={'kind':'inspect','loaded':True}
                elif args.scenario=='walk':value['scenario_result']=await scenarios.walk()
                elif args.scenario=='fishing':value['scenario_result']=await scenarios.fishing()
                elif args.scenario=='combat':value['scenario_result']=await scenarios.combat()
                else:value['scenario_result']=await scenarios.npc()
                if tasks:await asyncio.wait(tasks,timeout=20)
                value['server_terminal']=server_terminal(value['intents'],value['receipts'],args.scenario)
                if cache:value['asset_cache']=dict(cache.metrics)
            finally:
                if scenarios:await scenarios.close()
                await context.close();value['browser_closed']=True
    except (Exception,asyncio.CancelledError) as error:
        # Never persist launch URLs, cookies, initData or exception strings containing them.
        value['error_type']=type(error).__name__
        value['error_message']=re.sub(r'https?://\S+','[URL omitted]',str(error))[:400]
        value['unknown']=bool(value['intents'])
    finally:
        value['finished_at']=time.time();persist()
    print(json.dumps({k:value.get(k) for k in ['identity_id','scenario','scenario_result','error_type','pending','server_terminal','browser_closed']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url-file',type=Path,required=True)
    parser.add_argument('--expected-player-id',type=int,required=True)
    parser.add_argument('--scenario',choices=['inspect','walk','npc','fishing','combat'],required=True)
    parser.add_argument('--profile-dir',type=Path,required=True)
    parser.add_argument('--result',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(run(args))
