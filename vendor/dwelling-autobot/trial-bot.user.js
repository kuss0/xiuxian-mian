// ==UserScript==
// @name         洞府助手（主支线 + 每日副本 + 试炼）
// @namespace    dwelling-autobot
// @version      2.0.78
// @description  每日试炼固定一重，入场确认有奖才战斗，无奖立即退出，每波清场后回到开局位置；穆上师全程远程输出并按真实落点躲黑峰冰晶；月殿按当前装备、灵力与冷却轮换输出，巨剑打断血契；已取得御风车时起飞改乘御风车；题记高处可飞到正上方急降，地面无法起降处悬停题记；每日副本领奖检查与复查，结束返回洞府
// @match        https://asc.aiopenai.app/miniapp/xianxia-dwelling*
// @run-at       document-start
// @grant        unsafeWindow
// @grant        GM_xmlhttpRequest
// @connect      *
// ==/UserScript==

(function () {
  'use strict';
  const window = typeof unsafeWindow === 'undefined' ? globalThis.window : unsafeWindow;

  if (window.__autobot?.version === '2.0.78') return;
  const inherited = window.__autobot?.handoff?.();
  const sameScene = !!inherited && inherited.runtime === window.__rt && inherited.combat === window.__fc;
  // 更新已运行的助手时先松键、停旧战斗循环；保留游戏本身的试炼进度。
  window.__autobot?.stop?.();
  document.getElementById('dwellingAutobot')?.remove?.();
  // ============ 1. 捕获页面运行时（页面加载前装好） ============
  //   createDwelling3D          → window.__rt  场景/移动/镜头/任务
  //   createDwellingFieldCombat → window.__fc  战斗（敌人/技能/波次）
  // Proxy 保留函数的静态成员，以及页面在赋值之后添加的 version/supports*。
  // 同时处理脚本已下载、但工厂还未执行的情况。
  let questCatalogSource = sameScene && inherited?.quests === window.__quests ? inherited.questCatalogSource : null;
  let questCatalog = sameScene && inherited?.quests === window.__quests ? inherited.questCatalog : null;
  let questCatalogPending = null;
  let markedQuest = null;
  let fieldEnvironment = sameScene ? inherited?.environment || null : null;
  let flightRuntime = sameScene ? inherited?.flightRuntime || null : null;
  let fieldCombatRuntime = sameScene ? inherited.combat : null;
  let questRuntime = sameScene ? inherited.quests : null;
  let fieldSettlements = sameScene ? inherited.fieldSettlements || [] : [];
  let fieldSettlementSequence = fieldSettlements.at(-1)?.sequence || 0;
  const fieldSettlementOperations=new Set();
  let rewardRequests = sameScene ? inherited.rewardRequests || {} : {};
  {
    const hook = (name, slot) => {
      const descriptor = Object.getOwnPropertyDescriptor(window, name);
      if (descriptor && !descriptor.configurable) return;
      let impl = window[name];
      const wrap = fn => typeof fn !== 'function' ? fn : new Proxy(fn, {
        apply(target, receiver, args) {
          let tracked = null;
          if (slot === '__rt') {
            const options = args[1] || {};
            tracked = { runtime: null, access: options.flightAccess, info: null, request: null };
            args = [args[0], { ...options,
              onFlight(info) {
                tracked.info = info;
                options.onFlight?.(info);
                // 停止可能发生在服务端确认与游戏实际起飞之间。
                if (tracked.request?.cancelled && info.active && !['landing', 'dive'].includes(info.phase)) tracked.runtime?.dropFlight?.();
                if (tracked.request?.cancelled && !info.active && !info.starting) tracked.request = null;
              },
              ...(typeof options.onFlightStart === 'function' ? {
                async onFlightStart(available) {
                  const request = tracked.request;
                  const current = () => !request?.cancelled && available();
                  if (!current()) return null;
                  const result = await options.onFlightStart(current);
                  if (request?.cancelled) {
                    if (result?.sessionId) await options.onFlightStop?.(result.sessionId);
                    return null;
                  }
                  return result;
                },
              } : {}),
            }, ...args.slice(2)];
          }
          if (slot==='__fc' && typeof args[1]?.fieldRequest==='function') {
            const options=args[1],original=options.fieldRequest;
            args=[args[0],{...options,async fieldRequest(action,body,...rest){
              const data=await original.call(this,action,body,...rest);
              observeFieldSettlement('quests',{...body,action},data);return data;
            }},...args.slice(2)];
          }
          if (slot==='__fc' && typeof args[1]?.moonRequest==='function') {
            const options=args[1],original=options.moonRequest;
            args=[args[0],{...options,async moonRequest(action,body,...rest){
              const run=moonDiagnosticRequest(action,body);
              try {const data=await original.call(this,action,body,...rest);moonDiagnosticResponse(run,data);return data;}
              catch(error) {moonDiagnosticResponse(run,null,true);throw error;}
            }},...args.slice(2)];
          }
          const obj = Reflect.apply(target, receiver, args);
          if (slot==='environment')fieldEnvironment=obj;
          else window[slot] = obj;
          if (tracked) { tracked.runtime = obj; flightRuntime = tracked; }
          if (slot === '__fc') {
            fieldEnvironment = args[1]?.environment || null; fieldCombatRuntime = obj;
            hookDailyTrialStart(obj);
            rewardRequests = Object.fromEntries(['moon','blood','siege','escort','trial'].map(k => [k, args[1]?.[k + 'Request']]).filter(([,fn]) => typeof fn === 'function'));
          }
          if (slot === '__quests') {
            questRuntime = obj;
            questCatalogSource = args[0];
            questCatalog = null;
            questCatalogPending = null;
            markedQuest = null;
            const original = obj.mapTargets;
            if (typeof original === 'function') obj.mapTargets = function (...params) {
              const targets = original.apply(this, params);
              const row = questRows(true, null).find(q => q.id === markedQuest);
              if (row?.status === 'available' && row.contact?.position) {
                targets.push({ questId: row.id, title: row.title, number: row.number,
                  name: row.contact.name, position: row.contact.position.slice(), radius: 5,
                  objective: '向' + row.contact.name + '接取任务', kind: 'talk', tracked: true });
              }
              return targets;
            };
          }
          console.log('[自驾] 捕获 ' + name + ' → ' + slot);
          return obj;
        },
      });
      impl = wrap(impl);
      Object.defineProperty(window, name, {
        configurable: true,
        get() { return impl; },
        set(fn) { impl = wrap(fn); },
      });
    };
    hook('createDwellingEnvironment', 'environment');
    hook('createDwelling3D', '__rt');
    hook('createDwellingFieldCombat', '__fc');
    hook('createDwellingQuests', '__quests');
  }

  // ============ 1.5 画质强制最低 ============
  //   dwelling-quality.js：键 dwelling-graphics-quality-v1，值 'low' / 'balanced' / 'high'
  //   low = 流畅：30 帧、无阴影、4 光源、草木 0.35 —— 省 CPU/GPU，也省流量
  //   必须在页面脚本读配置之前写入 localStorage，所以放在 document-start 段里
  const QUALITY_KEY = 'dwelling-graphics-quality-v1';
  const QUALITY_TARGET = 'low';
  function applyLowQuality(tag) {
    try { localStorage.setItem(QUALITY_KEY, QUALITY_TARGET); } catch (e) {}
    const rt = window.__rt;
    if (!rt || typeof rt.setGraphicsQuality !== 'function') return null;
    try {
      const cur = typeof rt.graphicsQuality === 'function' ? rt.graphicsQuality() : null;
      if (cur && cur.key === QUALITY_TARGET) return cur;
      rt.setGraphicsQuality(QUALITY_TARGET);
      const now = typeof rt.graphicsQuality === 'function' ? rt.graphicsQuality() : null;
      console.log('[自驾] 画质锁定最低 → ' + (now ? now.name + '(' + now.key + ')' : '?') + (tag ? ' · ' + tag : ''));
      return now;
    } catch (e) { return null; }
  }
  applyLowQuality('页面加载前');
  // 运行期看门狗：有人手动调高了就再压回最低
  setInterval(() => { if (window.__rt) applyLowQuality(); }, 15000);

  // 性能采样收到 game_login_required 后退避，保留真实失败响应。
  // 凭据变化会立即重试；其他接口沿用原 fetch，不缓存任务、战斗或御空请求。
  const heartbeat = window.__dwellingAutobotHeartbeat || { status: 0, error: '', retryAt: 0, suppressed: 0 };
  if (!window.__dwellingAutobotHeartbeat && typeof window.fetch === 'function') {
    window.__dwellingAutobotHeartbeat = heartbeat;
    const nativeFetch = window.fetch;
    let credential = null, failed = null;
    window.fetch = async function (input, init) {
      let isSample = false, currentCredential;
      try {
        const url = new URL(typeof input === 'string' ? input : input.url, window.location.href);
        isSample = url.origin === window.location.origin && url.pathname === '/api/miniapp/observability/heartbeat' &&
          (init?.method || input?.method || 'GET').toUpperCase() === 'POST' && typeof init?.body === 'string';
        if (isSample) currentCredential = JSON.parse(init.body).initData;
      } catch { isSample = false; }
      if (!isSample) return nativeFetch.call(this, input, init);
      if (currentCredential !== credential) { credential = currentCredential; failed = null; heartbeat.retryAt = 0; }
      if (failed && Date.now() < heartbeat.retryAt) { heartbeat.suppressed++; return failed.clone(); }
      const response = await nativeFetch.call(this, input, init);
      // 旧请求晚到时不能覆盖新登录态的结果。
      if (credential !== currentCredential) return response;
      heartbeat.status = response.status;
      if (response.status === 401) {
        const data = await response.clone().json().catch(() => ({}));
        if (credential !== currentCredential) return response;
        if (data.error === 'game_login_required') {
          failed = response.clone(); heartbeat.error = data.error; heartbeat.retryAt = Date.now() + 300000;
          console.warn('[自驾] 性能心跳未获游戏登录授权，5 分钟后重试');
        } else { failed = null; heartbeat.error = ''; heartbeat.retryAt = 0; }
      } else { failed = null; heartbeat.error = ''; heartbeat.retryAt = 0; }
      return response;
    };
  }


  const REWARD_RESOURCES = {
    quests:{kind:'任务',name:'任务交付'},trial:{key:'trial',kind:'试炼',name:'试炼'},
    'moon-palace':{key:'moonPalace',kind:'副本',name:'月殿血誓'},
    'blood-land':{key:'bloodLand',kind:'副本',name:'血色禁地'},
    'south-gate':{key:'southGate',kind:'副本',name:'南门守城'},
    'refugee-escort':{key:'refugeeEscort',kind:'副本',name:'护民出关'},
    'mulan-shrine':{key:'mulanShrine',kind:'副本',name:'血祭神坛'},
    'baiqiao-puppet':{key:'baiqiaoPuppet',kind:'副本',name:'试傀场'},
    'jianzhong-trial':{key:'jianzhongTrial',kind:'副本',name:'剑冢'},
  };
  function rewardReceipt(resource, body, data) {
    const spec=REWARD_RESOURCES[resource], action=body?.action;
    if (!spec || typeof action!=='string' || data?.ok !== true || ['status','start','review','share','accept'].includes(action)) return null;
    const receipt=spec.key ? data[spec.key] : data;
    if (!receipt || receipt.error || receipt.pending || receipt.anomaly || receipt.practice || receipt.kind==='practice' || receipt.run?.practice || receipt.run?.practiceOnly || receipt.run?.rewarded===false) return null;
    if (resource==='quests' && !['turn-in','journal-discover'].includes(action)) return null;
    const rewards=(Array.isArray(receipt.grants) ? receipt.grants : []).filter(g=>g && typeof g.quantity==='number' && Number.isFinite(g.quantity) && g.quantity>0 && g.quantity<=1e15).map(g=>({
      name:String(g.name || data.quests?.rewardDefinitions?.[g.itemId]?.name || g.itemId || '物品').slice(0,80),quantity:g.quantity}));
    for (const [key,name] of [['cultivation','修为'],['spiritStones','灵石'],['merit','宗门贡献']]) {
      if (typeof receipt[key]==='number' && Number.isFinite(receipt[key]) && receipt[key]>0 && receipt[key]<=1e15) rewards.push({name,quantity:receipt[key]});
    }
    if (!rewards.length) return null;
    let id;
    if (resource==='quests') {
      const key=action==='journal-discover'?body.pointId:body.questId;
      if(!key)return null;id=(action==='journal-discover'?'journal:':'quests:')+key;
    }
    else {
      const run=body.runId || receipt.run?.id || receipt.runId;
      if (run) id=resource+':'+run+':'+action+(body.wave!=null?':'+body.wave:'');
      else if(body.operationId) id=resource+':'+body.operationId;
      else return null; // 没有稳定真实回执，不能用每次随机 ID 重复报奖。
    }
    if (!/^[A-Za-z0-9_.:-]{1,180}$/.test(id)) return null;
    let title=resource==='quests' ? questCatalog?.quests.find(q=>q.id===body.questId)?.title || body.questId : spec.name;
    if(action==='journal-discover')title=data.quests?.journal?.points?.find(p=>p.id===body.pointId)?.name || body.pointId;
    if(resource==='trial') title=({longwind:'麦田试炼',bamboo:'竹林试炼'})[body.arena || receipt.run?.arena] || '试炼';
    if(body.wave!=null) title+=' · 第'+(Number(body.wave)+1)+'波';
    return {event_id:id,kind:action==='journal-discover'?'题记':spec.kind,title:String(title).slice(0,80),rewards:rewards.slice(0,40)};
  }
  const FAILURE_CODES=['defeat','timeout','low_health','sync_error','navigation','load_failed','request_failed','runtime'];
  function safeLogText(value,limit=320) {
    return String(value ?? '').replace(/https?:\/\/[^\s<>]+/gi,'[链接已隐藏]')
      .replace(/\b(authorization|cookie)["']?\s*[:=][^\r\n]*/gi,'$1=[已隐藏]')
      .replace(/\b(token|initData|tgWebAppData|cookie|authorization|signature|hash|autobotToken|autobotLogUrl)["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;}]+)/gi,'$1=[已隐藏]')
      .replace(/[A-Za-z0-9_%+-]{32,}/g,'[长标识已隐藏]').replace(/\s+/g,' ').trim().slice(0,limit);
  }
  const rewardLogStatus={message:'请从控制台洞府入口打开以启用',pending:0,sent:0,rewards:0,errors:0};
  let rewardBridge=null, rewardQueue=[], rewardSending=false, rewardRetryAt=0, rewardFailures=0;
  function storedRewardEvent(event) {
    if(event?.kind==='异常') {
      if(!/^[A-Za-z0-9_.:-]{1,180}$/.test(event.event_id) || !FAILURE_CODES.includes(event.code) || !/^\d+(?:\.\d+){1,3}$/.test(event.version) ||
          typeof event.title!=='string' || !event.title.trim() || typeof event.message!=='string' || !event.message.trim()) return null;
      const result={event_id:event.event_id,kind:'异常',title:safeLogText(event.title,80),code:event.code,message:safeLogText(event.message),phase:safeLogText(event.phase,120),version:event.version};
      if(Number.isInteger(event.hp) && Number.isInteger(event.hp_max) && event.hp>=0 && event.hp<=event.hp_max && event.hp_max>0 && event.hp_max<=1e15)Object.assign(result,{hp:event.hp,hp_max:event.hp_max});
      return result;
    }
    if (!event || !/^[A-Za-z0-9_.:-]{1,180}$/.test(event.event_id) || !['任务','副本','试炼','题记'].includes(event.kind) ||
        typeof event.title!=='string' || !event.title.trim() || !Array.isArray(event.rewards) || !event.rewards.length || event.rewards.length>40) return null;
    if (event.rewards.some(r=>!r || typeof r.name!=='string' || !r.name.trim() || typeof r.quantity!=='number' || !Number.isFinite(r.quantity) || r.quantity<=0 || r.quantity>1e15)) return null;
    return {event_id:event.event_id,kind:event.kind,title:event.title.slice(0,80),
      rewards:event.rewards.map(r=>({name:r.name.slice(0,80),quantity:r.quantity}))};
  }
  try {
    const params=new URLSearchParams(window.location.hash.slice(1)), token=new URLSearchParams(window.location.search).get('autobotToken');
    const endpoint=new URL(params.get('autobotLogUrl'));
    const player=params.get('playerId');
    if (token && /^[A-Za-z0-9_-]{32,64}$/.test(token) && player && ['https:','http:'].includes(endpoint.protocol) && endpoint.pathname==='/api/dwelling-reward' && !endpoint.username && !endpoint.password && !endpoint.search && !endpoint.hash) {
      rewardBridge={token,player_id:player,url:endpoint.href,key:'dwellingRewardQueue:v1:'+player};
      try {
        const saved=JSON.parse(localStorage.getItem(rewardBridge.key)||'[]');
        const seen=new Set();
        if (Array.isArray(saved)) rewardQueue=saved.flatMap(row=>{
          const event=storedRewardEvent(row?.event);
          if (!event || !Number.isFinite(row.at) || row.at>Date.now() || Date.now()-row.at>=48*3600000 || seen.has(event.event_id)) return [];
          seen.add(event.event_id);return [{event,at:row.at}];
        });
      } catch { rewardQueue=[]; }
      rewardLogStatus.message=typeof GM_xmlhttpRequest==='function'?'已连接，推送领奖与异常':'新版本需保存油猴并刷新后启用推送';
    }
  } catch {rewardBridge=null;rewardQueue=[];}
  function persistRewardQueue() {
    rewardLogStatus.pending=rewardQueue.length;
    try {if(rewardBridge)localStorage.setItem(rewardBridge.key,JSON.stringify(rewardQueue));}catch{}
  }
  function sendRewardLog(event) {
    return new Promise((resolve,reject)=>{
      if(typeof GM_xmlhttpRequest!=='function') {reject(new Error('save_userscript'));return;}
      GM_xmlhttpRequest({method:'POST',url:rewardBridge.url,headers:{'Content-Type':'application/json'},anonymous:true,timeout:15000,
        data:JSON.stringify({token:rewardBridge.token,player_id:rewardBridge.player_id,...event}),
        onload:r=>{try{const body=JSON.parse(r.responseText);resolve({status:r.status,ok:r.status===200 && body?.ok===true});}catch{reject(new Error('log_response'));}},
        ontimeout:()=>reject(new Error('log_timeout')),onerror:()=>reject(new Error('log_offline'))});
    });
  }
  async function flushRewardQueue() {
    if(window.__dwellingRewardObserver!==observeReward || !rewardBridge || !rewardQueue.length || rewardSending || Date.now()<rewardRetryAt || typeof GM_xmlhttpRequest!=='function') return;
    rewardSending=true;
    try {
      const first=rewardQueue[0], result=await sendRewardLog(first.event);
      if(window.__dwellingRewardObserver!==observeReward) return;
      if(result.ok) {rewardQueue.shift();rewardFailures=0;rewardRetryAt=0;rewardLogStatus.sent++;
        rewardLogStatus[first.event.kind==='异常'?'errors':'rewards']++;
        rewardLogStatus.message='已推送 '+rewardLogStatus.sent+' 条（领奖 '+rewardLogStatus.rewards+' · 异常 '+rewardLogStatus.errors+'）';persistRewardQueue();}
      else if(result.status===401) {rewardRetryAt=Infinity;rewardLogStatus.message='领奖入口已过期，请从控制台重新打开洞府';}
      else throw new Error('log_retry');
    } catch {rewardFailures++;rewardRetryAt=Date.now()+Math.min(300000,1000*2**Math.min(rewardFailures,9));rewardLogStatus.message='日志连接失败，将自动补发';}
    finally {rewardSending=false;}
  }
  function observeFieldSettlement(resource, body, data) {
    if (resource!=='quests' || body?.action!=='field-defeat' || typeof body.enemyId!=='string' ||
        !/^[a-zA-Z0-9_:-]{1,120}$/.test(body.enemyId)) return;
    // 原生请求与 fetch 兜底可能观察到同一回执，只记录一次；操作 ID 仅留内存。
    if(typeof body.operationId==='string') {
      const key=body.operationId+':'+(data?.ok===true?'ok':String(data?.error||'unknown'));
      if(fieldSettlementOperations.has(key))return;
      fieldSettlementOperations.add(key);
      if(fieldSettlementOperations.size>80)fieldSettlementOperations.delete(fieldSettlementOperations.values().next().value);
    }
    // 只读原生回执；不保存凭据/位置、不改写上报、不把本地倒地认作完成。
    const row={sequence:++fieldSettlementSequence,at:Date.now(),enemyId:body.enemyId,ok:data?.ok===true,
      error:typeof data?.error==='string' && /^[a-zA-Z0-9_:-]{1,120}$/.test(data.error)?data.error:null};
    for (const key of ['damage','hits']) if (Number.isFinite(body.report?.[key]) && body.report[key]>=0 && body.report[key]<=1e15)
      row[key]=body.report[key];
    fieldSettlements.push(row);if(fieldSettlements.length>40)fieldSettlements.shift();
    if(!row.ok && row.error==='quest_enemy_combat_invalid')log('击杀结算拒绝：'+row.enemyId+' · '+row.error);
  }
  function observeReward(resource, body, data) {
    observeFieldSettlement(resource,body,data);
    if(!rewardBridge)return;
    if (typeof window.ownWorldIdentity==='function' && String(window.ownWorldIdentity())!==rewardBridge.player_id) {rewardLogStatus.message='角色身份已改变，请从控制台重新打开洞府';return;}
    const spec=REWARD_RESOURCES[resource],receipt=spec?.key?data?.[spec.key]:data;
    const error=data?.error || receipt?.error || receipt?.anomaly;
    // 次数用尽和已收集是正常停止条件；不把完整响应、请求体或游戏凭据送往日志群。
    if(spec && (data?.ok===false || error) && !/fishing_daily_limit_reached|journal_point_found|daily_limit_reached|already_claimed/.test(String(error))) {
      reportFailure(spec.name+'请求失败：'+safeLogText(typeof error==='string'?error:'服务端未确认'));
    }
    const event=rewardReceipt(resource,body,data);
    if(!event || rewardQueue.some(r=>r.event.event_id===event.event_id))return;
    rewardQueue.push({event,at:Date.now()});persistRewardQueue();void flushRewardQueue();
  }
  const failureSeen=window.__dwellingFailureSeen || (window.__dwellingFailureSeen=new Map());
  const failureSession=Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,9);
  const failureObjects=new WeakSet();
  let failureSequence=0, failureCombat=null, failureState='';
  function reportFailure(message,combat=readCombat()) {
    if(!rewardBridge || typeof window.ownWorldIdentity==='function' && String(window.ownWorldIdentity())!==rewardBridge.player_id)return;
    if(message && typeof message==='object') {
      if(message instanceof Cancelled || message.code==='fishing_daily_limit_reached' || failureObjects.has(message))return;
      failureObjects.add(message);message=message.message;
    }
    let text=safeLogText(message);if(!text)return;
    const code=/战败|失利|defeat/i.test(text)?'defeat':/气血|血量/.test(text)?'low_health':/超时|时限|长期/.test(text)?'timeout':
      /同步|结算|战绩|回执/.test(text)?'sync_error':/寻路|卡路|路线|落点|无法靠近|传送|区域入口|题记未能就近/.test(text)?'navigation':/加载|场景已关闭|场景.*切换/.test(text)?'load_failed':/请求|网络|登录|身份/.test(text)?'request_failed':'runtime';
    if(code==='defeat')text='角色战败，请在游戏内恢复后继续';
    const key=code+':'+text, now=Date.now();
    for(const [k,at] of failureSeen)if(now-at>=60000)failureSeen.delete(k);
    if(failureSeen.has(key))return;
    if(failureSeen.size>=128)failureSeen.delete(failureSeen.keys().next().value);
    failureSeen.set(key,now);
    const event={event_id:'error:'+failureSession+':'+(++failureSequence),kind:'异常',code,title:({defeat:'角色战败',low_health:'低血量保护',timeout:'操作超时',sync_error:'结算同步异常',navigation:'寻路异常',load_failed:'场景加载异常',request_failed:'请求失败',runtime:'助手运行异常'})[code],
      message:text,phase:safeLogText(state.phase,120),version:'2.0.78'};
    if(Number.isFinite(combat?.hp) && Number.isFinite(combat?.hpMax) && combat.hpMax>0 && combat.hpMax<=1e15)
      Object.assign(event,{hp:Math.max(0,Math.min(Math.ceil(combat.hp),Math.ceil(combat.hpMax))),hp_max:Math.ceil(combat.hpMax)});
    rewardQueue.push({event,at:now});persistRewardQueue();void flushRewardQueue();
  }
  function observeBattleFailure() {
    const combat=readCombat(),runtime=window.__fc;
    if(combat?.state==='defeat' && (runtime!==failureCombat || failureState!=='defeat'))reportFailure('角色战败，请在游戏内恢复后继续',combat);
    failureCombat=runtime;failureState=combat?.state || '';
  }
  window.__dwellingRewardObserver=observeReward;
  if(!window.__dwellingRewardFetchHook && typeof window.fetch==='function') {
    window.__dwellingRewardFetchHook=true;
    const original=window.fetch;
    window.fetch=async function(input,init) {
      let resource=null,body=null;
      try {
        const url=new URL(typeof input==='string'?input:input.url,window.location.href);
        const candidate=url.pathname.replace('/api/miniapp/xianxia-dwelling/','');
        if(url.origin===window.location.origin && url.pathname==='/api/miniapp/xianxia-dwelling/'+candidate && REWARD_RESOURCES[candidate] && typeof init?.body==='string') {
          body=JSON.parse(init.body);resource=candidate;
        }
      } catch {}
      let response;
      try {response=await original.call(this,input,init);}
      catch(error) {
        if(resource && error?.name!=='AbortError')window.__dwellingRewardObserver?.(resource,body,{ok:false,error:'网络请求失败'});
        throw error;
      }
      if(resource) {
        try {
          void response.clone().json().then(data=>window.__dwellingRewardObserver?.(resource,body,data)).catch(()=>{
            window.__dwellingRewardObserver?.(resource,body,{ok:false,error:'响应无法读取（HTTP '+response.status+'）'});
          });
        } catch {} // 观察失败绝不影响原游戏请求。
      }
      return response;
    };
  }
  persistRewardQueue();
  clearInterval(window.__dwellingRewardTimer);
  window.__dwellingRewardTimer=setInterval(()=>{observeBattleFailure();void flushRewardQueue();retryLoadoutRestore();},2000);

  // ============ 2. 战斗数值表（取自 dwelling-field-combat.js） ============
  // 敌人招式的作用距离 [min,max]
  const RANGE = {
    enemyAttack1: [0, 2.3], enemyAttack2: [0, 2.3], enemyThrust: [0, 2.8], enemyHeavy: [0, 2.4],
    shieldBash: [0, 2.0], spearJab2: [0, 3.0], spearSweep: [0, 3.1], enemyKick: [0, 1.9],
    bossFlurry: [0, 2.6], bossDash: [3.2, 9], bossLeap: [4, 9.5], bossQi: [5, 16], bowShot: [5, 22],
  };
  // 该怎么应对：sidestep = 侧闪（垂直于敌人出手方向），dodge = 必须翻滚，leave = 离开红圈，guard = 格挡
  const COUNTER = {
    enemyAttack1: 'sidestep', enemyAttack2: 'sidestep', enemyKick: 'sidestep',
    shieldBash: 'sidestep', spearJab2: 'sidestep', bossFlurry: 'leave',
    enemyThrust: 'sidestep', bowShot: 'sidestep',
    enemyHeavy: 'dodge', spearSweep: 'dodge', bossDash: 'dodge',
    bossLeap: 'leave', bossQi: 'dodge',
  };
  // 必须用翻滚（有无敌帧）躲的招式
  const MUST_ROLL = new Set(['enemyHeavy', 'spearSweep', 'bossDash', 'bossQi', 'bossLeap', 'bossFlurry']);
  // 三连斩用翻滚向外脱离整段攻击范围，不假设一次无敌能覆盖三次命中。
  // longwind-combat-v1.js 的实际接触/发射时间；侧翻无敌仅约 0.03–0.30 秒。
  const IMPACT = { enemyHeavy: [.94, 1.06], spearSweep: [.70, .84], bossDash: [.60, .72], bossQi: [.92, 1.12], bossLeap: [.90, 1.02] };

  const CFG = {
    tickMs: 90,            // 决策频率
    fallbackRadius: 34,    // 拿不到场地数据时，以进场点为中心的安全半径
    orbitRadius: 12,        // 绕「敌人群体中心」的半径（飞剑射程 34，够用）
    orbitBand: 2,          // 半径容差
    centerRadius: 16,      // 没有敌人时绕场心跑的半径
    retreatDistance: 10,  // 敌人进入十米内优先撤离，持续保持远程点杀
    keepAway: 3.5,         // 敌人贴到这个距离以内就侧后方让开
    orbitDir: 1,           // 绕行方向：+1 逆时针，-1 顺时针
    waveHomeRadius: 1.5,   // 每波清场后走回开局位置，进入此半径即停
    edgeMargin: 14,        // 最大边距；小场地最多预留半径的 30%，避免挤回敌群
    swordRange: 30,        // 飞剑点杀射程 34，超过这个距离先贴近
    potionHpBelow: 0.4,    // 气血低于 40% 喝血瓶
    bossPotionHpBelow: 0.65, // 首领连击伤害高，提前运化血瓶
    potionManaBelow: 25,   // 灵力低于 25 喝灵瓶
    pierceMana: 8,         // 飞剑点杀消耗
    fanMana: 25,           // 剑光分化消耗
    manaPotionAt: 25,      // 灵力低于此值就补灵
    fightTimeoutMs: 30 * 60 * 1000,
    settlementTimeoutMs: 180000,
    trialEntryTimeoutMs: 30000, // 等待本局原生开局回执/提示，不打完练习局才检查
    trialEntryMaxAttempts: 8, // 次数状态不可靠时，每次点击仍限制入场数量
    flightMinDistance: 40, // 与面板一致，按人物到目标的距离判断室外长途御空
    flightAltitude: 16,
    airHoverHeight: 10,    // 无法降落处在目标上空悬停的离地高度（御风车原生最低 4.5 米）
    airManaStart: .35,     // 进入无法降落的区域前，御空法力至少为上限的比例
    airManaAbort: .12,     // 低于此比例即中止航线，留出飞离的余量
    moonSwapHitPassive: true, // 月殿默认收起紫罗极火等放大命中数的被动，结束后自动祭回
    moonSkipHitPassive: true, // 收起失败或装备档案未知时跳过月殿，避免带被动进入并被服务端拒绝
  };

  const state = {
    running: false,
    busy: false,
    story: null,
    batch: null,
    generation: 0,
    phase: '就绪',
    error: '',
    result: null,
    driveOwner: null,
    combatOwner: null,
    driveStartedAt: 0,
    settlingAt: 0,
    expectedArena: null,
    timer: null,
    heldKeys: new Set(),
    lastKeyPulse: 0,
    orbitDir: CFG.orbitDir,
    lastAct: '',
    lastLockAt: 0,
    lastRollAt: -Infinity,
    skillRetryAt: {},
    evadeUntil: 0,
    gengCancelUntil: 0,
    log: [],
    trace: [],
    navigation: null,
    stats: { ticks: 0, pierces: 0, fans: 0, giants: 0, arts: 0, treasures: 0, ultimates: 0, rolls: 0, attacks: 0, dodges: 0, potions: 0, geng: 0, gengCancels: 0 },
    lastHp: null, damage: [],
    retry: { enabled:false, attempts:0, history:[] },
  };

  const skippedCombatSettlements = new WeakMap();

  // ============ 3. 底层输入 ============
  function canvas() { return document.getElementById('dwellingWorldCanvas'); }

  const KEY_CODE = { w: 'KeyW', a: 'KeyA', s: 'KeyS', d: 'KeyD', shift: 'ShiftLeft' };
  function keyEvent(type, key) {
    const el = canvas();
    if (!el) return;
    el.dispatchEvent(new KeyboardEvent(type, {
      key, code: KEY_CODE[key] || ('Key' + key.toUpperCase()),
      bubbles: true, cancelable: true, composed: true,
    }));
  }
  // 补差量并定期重申移动输入；交互、失焦等会清空游戏的按键状态。
  function setKeys(want) {
    const next = new Set(want);
    const now = performance.now();
    const pulse = next.size > 0 && now - state.lastKeyPulse >= 450;
    for (const k of state.heldKeys) if (!next.has(k)) keyEvent('keyup', k);
    for (const k of next) if (!state.heldKeys.has(k) || pulse) keyEvent('keydown', k);
    if (pulse) state.lastKeyPulse = now;
    state.heldKeys = next;
  }
  function releaseKeys() { setKeys([]); }

  // 技能：只走战斗控制器的 command()（UI 按钮和快捷键最终也是调它），
  // 返回 false 就是「这次没放出去」（冷却中/灵力不够/没有目标），
  // 不像 gameAction 那样按键成功也返回 true，避免误判成放了技能。
  function act(id) {
    const rt = window.__rt;
    if (!rt) return false;
    try {
      if (typeof rt.combatAction === 'function') return rt.combatAction(id) === true;
      if (typeof rt.gameAction === 'function') return rt.gameAction(id, true, false) === true;
    } catch (e) {}
    return false;
  }

  // ============ 4. 读取状态 ============
  function readCombat() {
    const fc = window.__fc;
    try { return typeof fc?.stats === 'function' ? fc.stats() : readWorld()?.combat || null; }
    catch (e) { return null; }
  }
  function readWorld() {
    const rt = window.__rt;
    if (!rt || typeof rt.stats !== 'function') return null;
    try { return rt.stats(); } catch (e) { return null; }
  }
  function myPos() {
    const w = readWorld();
    return w ? position3(w.actorPosition) : null;
  }
  // questLocations 返回 [x,z]，人物与敌人返回 [x,y,z]。入口处统一为三维。
  function position3(p) {
    if (!Array.isArray(p) || p.length < 2) return null;
    const z = p.length >= 3 ? p[2] : p[1];
    return Number.isFinite(p[0]) && Number.isFinite(z) ? [p[0], 0, z] : null;
  }
  function bearingTo(me, p) { return Math.atan2(p[0] - me[0], p[2] - me[2]); }
  function norm(a) { return Math.atan2(Math.sin(a), Math.cos(a)); }

  function dist(a, b) {
    if (!a || !b) return Infinity;
    return Math.hypot(a[0] - b[0], a[2] - b[2]);
  }
  function isBoss(e) {
    if (!e) return false;
    // 后续地区以内容包添加首领，沿用游戏的 boss 标记，不再逐个猜角色名称。
    return ['boss', 'bloodboss', 'flameelder', 'ghostelder', 'nangongque'].includes(e.role) ||
      (window.qingxiCombatContent || []).some(pack => pack.kinds?.[e.role]?.boss === true);
  }
  // ============ 5. 威胁判断（招式、距离与出手阶段） ============
  //   敌人起手时 field-combat 会亮起预警（扇形/直线/红圈），telegraph=true 就是「要打出来了」。
  //   同一次出手只响应一次，避免站在预警里反复翻滚。
  const lastReact = new Map();
  const qiFlights = new Map();
  const arrowFlights = new Map();
  let combatEscape = null;
  // 剑气的发射动作结束后，弹体仍以 15 m/s 飞行 1.25 秒；飞剑为 1.4 秒。
  // 保留观察到起手时的横移方向直到最迟飞行结束，不把收招当作安全。
  function qiThreats(s, me, now) {
    const clock = Number.isFinite(s.wave?.time) ? s.wave.time : now / 1000;
    for (const e of s.enemies || []) {
      if (e.hidden || e.returning || e.hp <= 0 || e.state !== 'attack' || e.move !== 'bossQi') continue;
      const old = qiFlights.get(e.id), started = clock - (e.time || 0);
      if (!old || Math.abs(old.started - started) > .25) {
        qiFlights.set(e.id, { started, expires:started + 1.12 + 1.4 + .15,
          origin:e.position.slice(), want:bearingTo(me,e.position) + state.orbitDir * Math.PI / 2,
          e:{...e,position:e.position.slice()}, reaction:{responded:false} });
      }
    }
    const out = [];
    for (const [id, shot] of qiFlights) {
      if (clock > shot.expires) { qiFlights.delete(id); continue; }
      const d = dist(me,shot.origin);
      if (d > 22) continue;
      const age = clock - shot.started;
      const arrival = .92 + d / (shot.e.role === 'swordcult' ? 17 : 15);
      out.push({e:shot.e,move:'bossQi',how:'dodge',dist:d,want:shot.want,
        mobile:age >= arrival - .55 && age <= arrival + .3,
        critical:age >= arrival - .18 && age <= arrival + .2, reaction:shot.reaction});
    }
    return out;
  }
  function arrowPathDistance(me, origin, aim) {
    const x = aim[0] - origin[0], z = aim[2] - origin[2], length = x*x + z*z;
    const t = length ? Math.max(0,Math.min(1,((me[0]-origin[0])*x+(me[2]-origin[2])*z)/length)) : 0;
    return dist(me,[origin[0]+t*x,0,origin[2]+t*z]);
  }
  // 原生飞剑真正接触的时刻：劈斩 2.6×.5；双剑三次穿越；血剑 2.4×(.36+.1×∛.55)。
  // 落点在起手时锁定，不能离开后立即忘记，也不能只躲双剑的第一段。
  const MOON_BLADES={yinyang:{hits:[1.3],radius:3.2},white:{hits:[.247,.754,1.261],radius:1.9},blood:{hits:[1.061],radius:2}};
  function moonEvasionWatch(s, me, now, view) {
    if (s.arena!=='moonpalace' || !state.story || !view) return null;
    const clock=Number.isFinite(s.wave?.time)?s.wave.time:now/1000, run=s.moonPalace?.run?.id || livingEnemies(s)[0]?.id;
    const count=Number(view.pools)||0;
    let watch=state.story.moonAvoid;
    if (!watch || watch.combat!==window.__fc || watch.run!==run || clock<watch.time || view.phase<watch.phase) {
      watch=state.story.moonAvoid={combat:window.__fc,run,time:clock,position:me.slice(),phase:view.phase,mode:view.mode,
        fightTime:0,shots:{},pools:[],poolCount:count,nextPoolAt:view.phase===3?(count?7:4):null,
        poolReaction:{responded:false},unknownUntil:count?12.2:0};
    }
    const dt=Math.max(0,clock-watch.time);
    if (watch.phase===3 && watch.mode==='fight') watch.fightTime+=dt;
    for (const [name,spec] of Object.entries(MOON_BLADES)) {
      const attacking=name==='white' ? view.swords?.white?.attacking || view.swords?.black?.attacking : view.swords?.[name]?.attacking;
      if (!attacking) {delete watch.shots[name];continue;}
      watch.shots[name] ||= {at:clock,origin:me.slice(),radius:spec.radius,hits:spec.hits.map(at=>({at,reaction:{responded:false}}))};
    }
    if (view.channel==null) watch.channel=null;
    else watch.channel ||= {reaction:{responded:false}};
    if (view.phase===3) {
      if (watch.phase!==3) {watch.nextPoolAt=watch.fightTime+4;watch.poolReaction={responded:false};}
      if (count>watch.poolCount) {
        // 每次实际出现都重新对时，避免原生逐帧冷却的舍入误差在长战斗里累积。
        const at=watch.fightTime-(dt<=.2?dt/2:0);
        // 新池在两次观察之间生成；用相邻位置中点并扩大余量，保留完整 12 秒，避免走回去。
        const center=me.map((v,i)=>(v+watch.position[i])/2), margin=.7+Math.min(1.5,dist(me,watch.position)/2);
        watch.pools.push({center,radius:2.6+margin,at,expires:watch.fightTime+12.2,reaction:{responded:false}});
        if (count-watch.poolCount>1 || dt>.5) watch.unknownUntil=watch.fightTime+12.2;
        watch.nextPoolAt=at+7;watch.poolReaction={responded:false};
      } else if (watch.nextPoolAt!=null && watch.fightTime>watch.nextPoolAt+.4) {
        // 数量漏采 / 中途接管导致预测过期：撤销这个时刻，等真实数量增加再同步，不能永久禁止长读条。
        watch.nextPoolAt=null;watch.unknownUntil=Math.max(watch.unknownUntil,watch.fightTime+12.2);
      }
      watch.pools=watch.pools.filter(pool=>pool.expires>watch.fightTime);
      if (!count && watch.poolCount) {watch.pools=[];watch.unknownUntil=0;}
    } else {watch.pools=[];watch.nextPoolAt=null;watch.unknownUntil=0;}
    watch.poolCount=count;watch.time=clock;watch.phase=view.phase;watch.mode=view.mode;watch.position=me.slice();
    return watch;
  }
  function moonHazardZones(watch=state.story?.moonAvoid) {
    const s=readCombat();
    if (!watch || watch.combat!==window.__fc || !s?.active || s.state!=='fighting' || s.arena!=='moonpalace' ||
      (s.moonPalace?.run?.id || livingEnemies(s)[0]?.id)!==watch.run || s.wave?.time<watch.time) return [];
    return [...Object.values(watch.shots).filter(shot=>watch.time-shot.at<=shot.hits.at(-1).at+.16)
      .map(shot=>({center:shot.origin.slice(),radius:shot.radius+.75,locked:true})),
      ...(watch.mode==='fight'?watch.pools:[]).map(pool=>({center:pool.center.slice(),radius:pool.radius,locked:true}))];
  }
  function moonThreats(s, me, now) {
    if (s.arena!=='moonpalace' || !state.story) return [];
    const view=window.qingxiNgqBoss?.stats?.(), watch=moonEvasionWatch(s,me,now,view), out=[], boss=livingEnemies(s)[0];
    if (!watch || !boss) return out;
    const away=center=>dist(me,center)<.5?bearingTo(me,boss.position)+state.orbitDir*Math.PI/2:bearingTo(me,center)+Math.PI;
    for (const [name,shot] of Object.entries(watch.shots)) {
      const age=watch.time-shot.at, hazard={center:shot.origin.slice(),radius:shot.radius+.75,locked:true};
      if (age>shot.hits.at(-1).at+.16 || dist(me,shot.origin)>hazard.radius+.4) continue;
      const hit=shot.hits.find(hit=>age<=hit.at+.08);
      out.push({e:boss,move:'ngq-'+name,how:'dodge',dist:dist(me,boss.position),want:away(shot.origin),hazard,mobile:true,
        critical:!!hit && age>=hit.at-.23 && age<=hit.at+.08,reaction:hit?.reaction || {responded:true}});
    }
    if (view.channel!=null) out.push({e:boss,move:'ngq-channel',how:'dodge',dist:dist(me,boss.position),
      want:bearingTo(me,boss.position)+state.orbitDir*Math.PI/2,mobile:true,
      critical:view.channel>=2.78 && view.channel<3.08,reaction:watch.channel.reaction});
    if (view.phase===3 && view.mode==='fight') {
      const eta=watch.nextPoolAt==null?Infinity:watch.nextPoolAt-watch.fightTime;
      if (eta<=.32 && eta>=-.2) out.push({e:boss,move:'ngq-pool-spawn',how:'dodge',dist:dist(me,boss.position),
        want:bearingTo(me,boss.position)+state.orbitDir*Math.PI/2,mobile:true,critical:eta<=.23,reaction:watch.poolReaction});
      for (const pool of watch.pools) {
        const inside=dist(me,pool.center)<pool.radius;
        if (!inside) {pool.reaction={responded:false};continue;}
        out.push({e:boss,move:'ngq-pool',how:'leave',dist:dist(me,boss.position),want:away(pool.center),
          hazard:{center:pool.center.slice(),radius:pool.radius,locked:true},mobile:true,critical:true,reaction:pool.reaction});
      }
    }
    // 已响应的剑段不能盖住同一帧尚未响应的血契 / 下一剑段。
    return out.sort((a,b)=>Number(b.critical && !b.reaction.responded)-Number(a.critical && !a.reaction.responded));
  }
  function yufengThreats(s, me) {
    const boss = livingEnemies(s).find(e => e.role === 'yufeng_mu'), view = boss?.behavior;
    if (!state.story || !view || !Number.isFinite(view.time)) return [];
    let track = state.story.muThreat;
    if (!track || track.id !== boss.id || view.time < track.time) {
      track = state.story.muThreat = {id:boss.id,time:view.time,position:me.slice(),mode:null,cast:null,
        peaks:view.counts?.peak||0,peakImpacts:view.counts?.peakImpacts||0,safeUntil:0};
    }
    const landed=(view.counts?.peakImpacts||0)>track.peakImpacts;
    if (landed) {track.safeUntil=view.time+3;track.cast=null;}
    track.peakImpacts=view.counts?.peakImpacts||0;
    const dt = view.time-track.time, velocity = dt>0 && dt<.4 ? [(me[0]-track.position[0])/dt,0,(me[2]-track.position[2])/dt] : [0,0,0];
    const newPeak=(view.counts?.peak||0)>track.peaks;
    if (!landed && (newPeak || view.mode === 'cast' && track.mode !== 'cast')) {
      track.cast = {at:view.time,type:newPeak||view.vfx?.peak?'peak':view.anim==='cast2'?'ice':'beam',aim:me.slice(),
        ice:view.counts?.ice||0,lockedAt:null,
        heading:bearingTo(me,boss.position)+state.orbitDir*Math.PI/2,reaction:{responded:false}};
    }
    const cast = track.cast;
    // 冰晶收招后至少还有 2.3 秒才再起手；实际在途冰晶仍由下方预警保护。
    if (cast?.type==='ice' && track.mode==='cast' && view.mode==='recover') track.safeUntil=view.time+1.8;
    track.mode=view.mode; track.time=view.time; track.position=me.slice();track.peaks=view.counts?.peak||0;
    if (!cast) return [];
    const type=view.vfx?.peak?'peak':view.vfx?.crystals>0&&cast.type!=='peak'?'ice':cast.type;
    if (type!==cast.type) {cast.type=type;cast.lockedAt=null;cast.escape=null;cast.early=null;}
    const age=view.time-cast.at, peak=cast.type==='peak', beam=cast.type==='beam';
    const lockAt=peak?2.2:beam?0:2.05;
    // 原生黑峰移到头顶时转 idle，冰晶发射时 ice 计数递增；用这些信号校正采样延迟。
    const locked=peak&&view.anim==='idle' || !peak&&!beam&&(view.counts?.ice||0)>cast.ice;
    if (cast.lockedAt==null && (locked || age>=lockAt)) {
      cast.lockedAt=locked?view.time:cast.at+lockAt;
      // 原生 lockedAim 只有宿主提供 playerVelocity 时才有提前量，现版宿主没有提供：
      // 落点就是锁定瞬间人物所在的位置。按采样延迟把当前位置回推到锁定时刻。
      const late=locked ? (dt>0&&dt<.4?dt/2:0) : Math.max(0,Math.min(.2,view.time-(cast.at+lockAt)));
      cast.aim=[me[0]-velocity[0]*late,me[1],me[2]-velocity[2]*late];
      // 锁定后沿原方向继续走是最快离开落点的路线；掉头会在落点附近损失整段转身时间。
      cast.escape=!beam && Math.hypot(velocity[0],velocity[2])>.5 ? Math.atan2(velocity[0],velocity[2]) : cast.heading;
    }
    if (cast.lockedAt==null) cast.aim=me.slice();
    const distance=dist(me,boss.position), impact=beam?.55:(cast.lockedAt??cast.at+lockAt)-cast.at+(peak?1.3:distance/26);
    const expires=peak?4.6:beam?.9:4.2;
    if (age>expires || view.mode!=='cast' && !view.vfx?.crystals && !view.vfx?.peak) {track.cast=null;return [];}
    const radius=peak?7.2:beam?1.8:4.8;
    const within=beam ? distance<11 && arrowPathDistance(me,boss.position,cast.aim)<1.8 : dist(me,cast.aim)<radius;
    if (beam && !within) return [];
    // 锁定后始终背离落点（离开后也保持该方向直到落地/弹体消失），避免恢复绕圈又走回阴影。
    const want=cast.lockedAt==null ? cast.heading : beam || dist(me,cast.aim)<.6 ? cast.escape : bearingTo(cast.aim,me);
    const closing=within && age>=impact-(peak?.4:.3) && age<=impact+.35;
    // 黑峰锁定后按当前速度走不出落点（被减速、被地形挡住）时，先翻滚一次拉开距离，落地前的翻滚仍保留。
    const speed=Math.hypot(velocity[0],velocity[2]);
    const early=peak && cast.lockedAt!=null && !closing && age<impact-1 && (cast.early ||= {responded:false}) && !cast.early.responded &&
      dist(me,cast.aim)+speed*(impact-age)<6.2;
    return [{e:boss,move:'yufeng-'+cast.type,how:'leave',dist:distance,
      want,mobile:true,
      critical:closing || early,reaction:early?cast.early:cast.reaction,
      hazard:{center:cast.aim.slice(),radius,locked:cast.lockedAt!=null}}];
  }
  // 穆上师全程远程输出，不再追近重击破盾。原生规则（adjust）：登车（引导/上车/驾车）期间，
  // 非轻击的直接命中立即打断（飞剑与持续伤害算轻击）；四面冰盾被打光后轻击也能打断。
  // 未打断前原生锁 1 点血，这里只读原生打断计数供核对，不改任何机制。
  function yufengObserve(target) {
    if (!state.story || target?.role!=='yufeng_mu') return;
    const view=target.behavior;
    if (!view || !Number.isFinite(view.time)) return;
    const interrupts=Number.isFinite(view.counts?.interrupts)?view.counts.interrupts:0;
    let plan=state.story.muMechanic;
    if (!plan || plan.id!==target.id || view.time<plan.time || view.phase<plan.phase || interrupts<plan.interrupts) {
      plan=state.story.muMechanic={id:target.id,time:view.time,phase:view.phase,interrupts,status:'远程输出',confirmedAt:null};
    }
    if (interrupts>plan.interrupts) {
      plan.confirmedAt=view.time;plan.status='原生确认打断';
      log('穆上师：原生确认打断登车（'+interrupts+'次）');
    } else if (view.phase===3 && !interrupts && plan.status!=='远程输出，等待打断登车') {
      plan.status='远程输出，等待打断登车';log('穆上师：第三阶段，继续远程输出打断登车');
    }
    plan.time=view.time;plan.phase=view.phase;plan.interrupts=interrupts;
  }
  function yufengSkills(s, target, requireRange = true) {
    if (!state.story || target?.role!=='yufeng_mu') return [];
    const distance=dist(myPos(),target.position);
    return (s.kit||[]).flatMap(slot=>{
      const rule=window.QingxiTreasureRules?.skills?.[slot.skill] || window.QingxiTreasureRules?.templates?.[slot.category];
      if (!['chisi','yinyang_swords','qianlan','light'].includes(slot.skill) && slot.category!=='light') return [];
      if (!rule || slot.locked || !Number.isFinite(slot.cost) || !(rule.range>0) || requireRange&&rule.range<distance+.6 || !(rule.damage>0)) return [];
      return [{slot,rule,duration:s.empowered?0:rule.cast||0}];
    });
  }
  function yufengManaReserve(s, target) {
    const choices=yufengSkills(s,target,false);
    return choices.length?Math.min(...choices.map(c=>c.slot.cost)):0;
  }
  function yufengBurst(s, target, step, now) {
    if (!state.story || target?.role!=='yufeng_mu') return false;
    const view=target.behavior, track=state.story.muThreat;
    if (!view || !track) return false;
    const escape=['channel','board','ride'].includes(view.mode);
    const safeUntil=escape ? Math.max(view.channelEnd||0,view.time+1.7) : view.mode==='stagger' ? view.openUntil : track.safeUntil;
    const unsafe=step.critical || step.mobile || s.paused || s.airborne || s.target!==target.id || view.vfx?.crystals>0 || dist(myPos(),target.position)<11;
    let plan=state.story.muCast;
    if (unsafe || !Number.isFinite(safeUntil) || safeUntil<=view.time+.2) {state.story.muCast=null;return false;}
    if (plan) {
      if (s.casting) {releaseKeys();return true;}
      if (plan.fired || now-plan.at>700) {state.story.muCast=null;return false;}
      const here=myPos(), still=dist(here,plan.position)<.03;
      plan.position=here.slice();releaseKeys();
      if (!still || now-plan.at<90) return true;
      const slot=(s.kit||[]).find(k=>k.slot===plan.slot);
      if (!slot || slot.locked || (slot.cooldown??0)>0 || s.gcdLeft>0 || s.mana<slot.cost || view.time+plan.duration+.15>safeUntil) {state.story.muCast=null;return false;}
      plan.fired=combatAction('slot'+slot.slot,'treasures',now);
      if (!plan.fired) {state.story.muCast=null;return false;}
      state.lastAct=slot.name;log('穆上师空档输出 '+slot.name);return true;
    }
    if (s.casting || s.gcdLeft>0 || s.action && s.action.kind!=='cast') return false;
    // 先让冰晶和黑峰的真实机制发生，避免反复打断导致阶段锁血。
    if (!escape && ((view.phase===1 && target.hp<=target.maxHp*.675 && !view.counts?.ice) ||
        (view.phase===2 && target.hp<=target.maxHp*.315 && !view.counts?.peakImpacts))) return false;
    const choices=yufengSkills(s,target).flatMap(({slot,rule,duration})=>{
      if ((slot.cooldown??0)>0 || s.mana<slot.cost || duration+view.time+.35>safeUntil || now<(state.skillRetryAt['slot'+slot.slot]||0)) return [];
      return [{slot,rule,duration,score:escape ? -duration : (rule.damage+2*(rule.dot||0))/Math.max(1,slot.cost)}];
    }).sort((a,b)=>b.score-a.score);
    const chosen=choices[0];if(!chosen)return false;
    if (!chosen.duration) {
      if (!combatAction('slot'+chosen.slot.slot,'treasures',now))return false;
      state.lastAct=chosen.slot.name;log('穆上师法宝输出 '+chosen.slot.name);return true;
    }
    releaseKeys();state.story.muCast={slot:chosen.slot.slot,duration:chosen.duration,at:now,position:myPos().slice(),fired:false};return true;
  }
  // 困阵 / 火莲：原生在施放瞬间把圈放在人物脚下（castTrap(e, player)），之后不再移动，delay 秒后合拢，
  // 仍在半径内就吃一次不可格挡的重击（翻滚无敌帧可躲）。stats 没有圈的坐标，只有 treasureFx 里的 'trap'
  // 和 metrics.traps 计数：计数增加的那一拍，人物在上一拍与这一拍之间的位置就是圈心。
  const TRAP_RULES = {arraymaster:{radius:2.6,delay:1.4}, flamearray:{radius:2.8,delay:1.3}, flameelder:{radius:3.2,delay:1.25}};
  let trapWatch = null;
  function trapRule(s) {
    let radius = 0, delay = Infinity;
    for (const e of s.enemies || []) {
      const rule = TRAP_RULES[e.role] || (window.qingxiCombatContent || []).find(pack => pack.kinds?.[e.role])?.kinds[e.role].trap;
      if (!(rule?.radius > 0)) continue;
      radius = Math.max(radius, rule.radius); delay = Math.min(delay, rule.delay > 0 ? rule.delay : 1.25);
    }
    // 布阵的敌人已倒下或读不到资料时按最大的圈处理。
    return radius ? {radius, delay} : {radius:3.2, delay:1.25};
  }
  function trapZones(s, me, now) {
    const count = Number(s.metrics?.traps) || 0, shown = (s.treasureFx || []).includes('trap');
    const clock = Number.isFinite(s.wave?.time) ? s.wave.time : now / 1000;
    if (!trapWatch || trapWatch.combat !== window.__fc || count < trapWatch.count || clock < trapWatch.clock - .5) {
      trapWatch = {combat:window.__fc, count, clock, position:me.slice(), zones:[]};
    }
    const gap = clock - trapWatch.clock;
    // 两拍之间隔得太久（过场、寻路中没有逐帧观察）就不知道圈放在哪，只沿用「场上有阵就保持移动」。
    if (count > trapWatch.count && gap <= .45) {
      const rule = trapRule(s), previous = trapWatch.position, moved = Math.min(3, dist(previous, me));
      const center = [(previous[0] + me[0]) / 2, 0, (previous[2] + me[2]) / 2];
      for (let i = trapWatch.count; i < count; i++) {
        trapWatch.zones.push({center, radius:rule.radius + moved / 2, at:clock - gap / 2, delay:rule.delay, reaction:{responded:false}});
      }
    }
    trapWatch.count = count; trapWatch.clock = clock; trapWatch.position = me.slice();
    trapWatch.zones = trapWatch.zones.filter(zone => shown && clock - zone.at < zone.delay + .12);
    return trapWatch.zones.map(zone => ({...zone, left:zone.at + zone.delay - clock}));
  }
  function trapThreats(s, me, now) {
    const out = [];
    for (const zone of trapZones(s, me, now)) {
      const d = dist(me, zone.center), margin = .7;
      if (d >= zone.radius + margin) continue;
      const foe = nearestEnemy(s, me)?.e || {position:zone.center, role:'trap', id:'trap'};
      // 出圈方向：离圈心最近的那条半径（跑动中被套上圈时就是原来的前进方向，不用掉头），
      // 再偏离近处的敌人一些；站着不动正踩在圈心时直接背向敌人。
      const out0 = d > .1 ? [(me[0] - zone.center[0]) / d, (me[2] - zone.center[2]) / d] : [0, 0];
      const fd = dist(me, foe.position), away = fd > .01 && fd < 10 ? [(me[0] - foe.position[0]) / fd, (me[2] - foe.position[2]) / fd] : [0, 0];
      const x = out0[0] + away[0] * (d > .1 ? .6 : 1), z = out0[1] + away[1] * (d > .1 ? .6 : 1);
      const want = Math.hypot(x, z) > .05 ? Math.atan2(x, z) : undefined;
      // 走不出去（被打出硬直、被挤住）时，在合拢前 0.25 秒内翻滚，用无敌帧躲过这一下。
      const inside = d < zone.radius + .15;
      out.push({e:foe, move:'trap', how:'leave', dist:d - zone.radius - margin, want, mobile:true,
        critical:inside && zone.left <= .24 && zone.left >= .04, reaction:zone.reaction,
        hazard:{center:zone.center.slice(), radius:zone.radius + margin, locked:true}});
    }
    return out;
  }
  // 地面上不能走进去的圈：困阵 / 火莲、未落完的月殿飞剑与血池。追击、绕行、场边滑行同样受约束。
  // （火符箭落点也试过当作圈来绕，模拟里换来的是更多近战受击，没有采用。）
  function groundHazards() {
    return [...(trapWatch?.combat === window.__fc ? trapWatch.zones : []).map(zone => ({center:zone.center.slice(), radius:zone.radius + .7, locked:true})), ...moonHazardZones()];
  }
  function threats(s, me, now) {
    const out = [...qiThreats(s, me, now), ...moonThreats(s, me, now), ...yufengThreats(s, me), ...trapThreats(s, me, now)];
    // 未暴露真实箭路：以出手期间最近观察到的位置保守估计。收招后离开箭路与落点即可输出。
    if ((s.enemies || []).some(e => e.move === 'bowShot') || arrowFlights.size) {
      const clock = Number.isFinite(s.wave?.time) ? s.wave.time : now / 1000;
      for (const e of s.enemies || []) {
        if (e.hidden || e.returning || e.hp <= 0 || e.state !== 'attack' || e.move !== 'bowShot') continue;
        const old = arrowFlights.get(e.id), started = clock - (e.time || 0);
        const shot = old && Math.abs(old.started - started) < .25 ? old : {
          started, want:bearingTo(me,e.position) + state.orbitDir * Math.PI / 2,
          e:{...e,position:e.position.slice()}, reaction:{responded:false} };
        shot.aim = me.slice(); shot.observedAt = clock;
        shot.expires = clock + 1.1;
        arrowFlights.set(e.id,shot);
      }
      for (const [id,shot] of arrowFlights) {
        if (clock > shot.expires) { arrowFlights.delete(id); continue; }
        const d = dist(me,shot.e.position);
        const attacking = (s.enemies || []).some(e => e.id === id && e.state === 'attack' && e.move === 'bowShot');
        const onPath = !shot.aim || arrowPathDistance(me,shot.e.position,shot.aim) < 3.2;
        if (d <= 30) out.push({e:shot.e,move:'bowShot',how:'sidestep',dist:d,want:shot.want,
          mobile:attacking ? clock - shot.started >= .35 : onPath,critical:false,reaction:shot.reaction});
      }
    }
    for (const e of s.enemies || []) {
      if (e.move === 'bossQi' || e.move === 'bowShot') continue;
      // 半血护体冲击独立于攻击状态，范围 3.6 米；先走出圈，不提前耗掉无敌帧。
      if (!e.hidden && !e.returning && e.hp > 0 && e.state === 'transition' && dist(me,e.position) < 4.2) {
        out.push({e,move:'phaseShock',how:'leave',dist:dist(me,e.position),
          want:bearingTo(me,e.position)+Math.PI,mobile:true,critical:false,reaction:{responded:false}});
        continue;
      }
      if (e.hidden || e.hp <= 0 || e.returning || e.state !== 'attack') { lastReact.delete(e.id); continue; }
      let reaction = lastReact.get(e.id);
      if (!reaction || reaction.move !== e.move || e.time < reaction.time - .05) {
        reaction = { move: e.move, time: e.time || 0, responded: false }; lastReact.set(e.id, reaction);
      }
      reaction.time = e.time || 0;
      // 预警灯（扇形/直线/红圈）亮起 = 已经进入出手前摇；有些招式没有预警，
      // 就用出手进度兜底（0.35s 之后必定已经起手）
      if (!e.telegraph && !(e.time >= 0.35) && e.move !== 'bossFlurry') continue;
      const r = RANGE[e.move];
      // 未知近身预警保留防御，远处未知提示不触发整段停火。
      if (!r && (!e.telegraph || dist(me, e.position) > 4)) continue;
      const d = dist(me, e.position);
      // RANGE 下限是敌人选择招式的起手距离；突进贴近后仍然有伤害。
      if (r && d > r[1] + .6) continue;
      const impact = IMPACT[e.move];
      if (impact && e.time > impact[1] + .08) continue;
      const critical = (MUST_ROLL.has(e.move) || !r) && (!impact || !e.tell && e.time >= impact[0] - .18);
      out.push({ e, move: e.move, how: COUNTER[e.move] || 'dodge', dist: d, critical, reaction });
    }
    out.sort((a, b) => Number(b.critical) - Number(a.critical) || a.dist - b.dist);
    return out;
  }

  // ============ 6. 走位：按游戏 moveYaw / walkX / walkZ 公式选择按键 ============
  const KEY_SETS = [['w'], ['w', 'd'], ['d'], ['s', 'd'], ['s'], ['a', 's'], ['a'], ['w', 'a']];
  function movementBasis(w) {
    const cf = w.cameraFollow || {};
    if (cf.active && !cf.mobile && Number.isFinite(cf.inputBasis)) return cf.inputBasis;
    if (!cf.mobile && Number.isFinite(w.orbitBasis)) return w.orbitBasis;
    return Number.isFinite(w.yaw) ? w.yaw : 0;
  }
  function keyHeading(keys, basis) {
    const forward = Number(keys.includes('w')) - Number(keys.includes('s'));
    const right = Number(keys.includes('d')) - Number(keys.includes('a'));
    return Math.atan2(-Math.sin(basis) * forward + Math.cos(basis) * right,
      -Math.cos(basis) * forward - Math.sin(basis) * right);
  }
  function steer(wantHeading) {
    if (!Number.isFinite(wantHeading)) { releaseKeys(); return null; }
    const w = readWorld() || {};
    const basis = movementBasis(w);
    let best = KEY_SETS[0], bestD = Infinity;
    for (const keys of KEY_SETS) {
      const error = Math.abs(norm(keyHeading(keys, basis) - wantHeading));
      if (error < bestD) { bestD = error; best = keys; }
    }
    // 连续按键即可转向，不需要先向八个任意方向试走。
    setKeys(best);
    return { keys: best.join(''), basis, heading: keyHeading(best, basis), want: wantHeading };
  }

  // 试炼场地：createDwellingFieldCombat.questLocations() 里有每个场地的场心与半径
  // 场地中心：开打那一刻记一次就冻住，之后一律用它判断「有没有跑出去」
  let arenaInfo = null;
  function captureArena(s, me) {
    if (arenaInfo) return arenaInfo;
    try {
      const fn = window.createDwellingFieldCombat.questLocations;
      const loc = typeof fn === 'function' ? fn() : {};
      const a = loc[s.arena];
      const center = position3(a && a.position);
      if (center && Number.isFinite(a.radius) && a.radius > 0) {
        arenaInfo = { center, radius: a.radius, src: 'arena' };
      }
    } catch (e) {}
    if (!arenaInfo) {
      // 拿不到场地数据就退而求其次：以进场点为中心
      arenaInfo = { center: [me[0], 0, me[2]], radius: CFG.fallbackRadius, src: 'entry' };
    }
    console.log('[自驾] 场地中心 ' + JSON.stringify(arenaInfo));
    return arenaInfo;
  }
  // 试炼每波清场后回到本局开局时的位置。新开局记录开战时的落点；
  // 接管打到一半的局拿不到开局落点，退而用场心。同一局的本波重试沿用原记录。
  function waveHome(s, me) {
    const home=state.waveHome;
    if (home && home.combat===window.__fc && home.arena===s.arena) return home.point;
    const fresh=(s.wave?.index || 0)===0 && !(s.wave?.time>5);
    const arena=captureArena(s,me);
    const point=fresh || arena.src!=='arena' ? [me[0],0,me[2]] : arena.center.slice();
    state.waveHome={combat:window.__fc,arena:s.arena,point,source:fresh?'entry':'center'};
    log('试炼开局位置：'+JSON.stringify(point.map(n=>Math.round(n*10)/10))+(fresh?'':'（接管中途，使用场心）'));
    return point;
  }
  function returnToWaveHome(s, me) {
    const home=waveHome(s,me), d=dist(me,home);
    if (d<=CFG.waveHomeRadius) { state.waveHomePath=null; return false; }
    const world=readWorld(), p=world?.actorPosition;
    state.phase='回到开局位置（'+Math.round(d)+' 米）';
    // 有碰撞数据（竹林的竹丛等）时用寻路绕开障碍；没有时直接朝开局位置走。
    if (!p || !fieldEnvironment?.canWalk || !fieldEnvironment?.groundHeight) return !!steer(bearingTo(me,home));
    const goal=[home[0],fieldEnvironment.groundHeight(home[0],home[2],p[1]),home[2]], now=performance.now();
    let route=state.waveHomePath;
    // 寻路结果（含“走不到”）缓存 1.5 秒，避免每帧重算。
    if (!route || route.combat!==window.__fc || route.home!==home || now-route.at>1500) {
      route=state.waveHomePath={combat:window.__fc,home,at:now,path:findPath(p,goal,CFG.waveHomeRadius)};
    }
    const path=route.path;
    // 找不到路线：竹林里原地等，不顶着竹丛硬走；开阔场地直接朝开局位置走。
    if (!path?.length) return s.arena!=='bamboo' && !!steer(bearingTo(me,home));
    while (path.length>1 && dist(p,path[0])<.85) path.shift();
    for (let i=Math.min(path.length-1,12); i>0; i--) {
      if (walkSegment(p,path[i],fieldEnvironment,[])) { path.splice(0,i); break; }
    }
    const input=chooseWalkInput(world,path[0],fieldEnvironment,[]);
    if (!input) return false;
    setKeys(input.keys);
    return true;
  }
  function arenaSafeRadius() {
    return arenaInfo.radius - Math.min(CFG.edgeMargin, arenaInfo.radius * .3);
  }
  // 场边沿原绕行方向向内收拢，避免突然直冲场心、穿回首领和剑气路线。
  function keepInside(me, want) {
    if (!arenaInfo) return want;
    const c = arenaInfo.center;
    const dc = dist(me, c);
    const safe = arenaSafeRadius();
    if (dc <= safe) return want;
    const inward = bearingTo(me,c), relative = norm(want - inward);
    const pull = Math.min(3, .45 + Math.max(0,dc-safe)/3);
    const maxAngle = Math.atan2(1,pull);
    if (Math.abs(relative) <= maxAngle) return want;
    const direction = Math.abs(Math.sin(relative)) > .05 ? Math.sign(Math.sin(relative)) : state.orbitDir;
    return inward + direction * maxAngle;
  }

  function nearestEnemy(s, me) {
    let best = null, bestD = Infinity;
    for (const e of s.enemies || []) {
      if (e.hidden || e.hp <= 0 || e.returning) continue;
      const d = dist(me, e.position);
      if (d < bestD) { bestD = d; best = e; }
    }
    return best ? { e: best, d: bestD } : null;
  }
  function attackTarget(s, me) {
    const target = (s.enemies || []).find(e => e.id === s.target && !e.hidden && !e.returning && e.hp > 0);
    return target ? { e: target, d: dist(me, target.position) } : nearestEnemy(s, me);
  }

  // 竹林优先消除远程压力；保留同类当前目标，避免来回切换分散伤害。
  function preferredEnemy(s, me) {
    const rank = e => e.role === 'talisman' ? 0 : e.role === 'swordcult' ? 1 : 2;
    const list = (s.enemies || []).filter(e => !e.hidden && !e.returning && e.hp > 0 && dist(me,e.position) < 32);
    list.sort((a,b) => rank(a)-rank(b) || a.hp-b.hp || dist(me,a.position)-dist(me,b.position));
    const current = list.find(e => e.id === s.target);
    return current && list[0] && rank(current) === rank(list[0]) ? current : list[0];
  }
  function focusRanged(s, me, now) {
    if (s.arena !== 'bamboo' || now < (state.focusRetryAt || 0)) return s;
    const wanted = preferredEnemy(s,me);
    if (!wanted || wanted.id === s.target) return s;
    // 普通 Tab 在 1.5 游戏秒内循环附近目标。每轮有上限，命令拒绝/目标不变立即结束。
    let current = s;
    for (let n=0;n<=(s.enemies || []).length;n++) {
      if (!act('lock')) break;
      const next = readCombat();
      if (!next) break;
      if (next.target === wanted.id) return next;
      if (next.target === current.target) break;
      current = next;
    }
    state.focusRetryAt = now + 500;
    return current;
  }

  // 优先沿攻击目标的圆周横移，只加有限的向外分量；碰撞、近战距离和点杀射程共同约束路径。
  function retreatHeading(s, me, fallback) {
    const enemies = (s.enemies || []).filter(e => !e.hidden && !e.returning && e.hp > 0);
    const nearest = nearestEnemy(s,me), target = attackTarget(s,me);
    if (!nearest || !target) return fallback;
    const bearing = bearingTo(me,target.e.position);
    const radial = Math.max(-.65,Math.min(.65,(target.d - (isBoss(target.e) ? 18 : CFG.orbitRadius))/3));
    const preferred = bearing + state.orbitDir*Math.atan2(1,radial);
    const world = readWorld() || {}, basis = movementBasis(world), p = world.actorPosition || me;
    const candidates = [];
    for (const keys of KEY_SETS) {
      const heading = keyHeading(keys,basis);
      const point = [p[0]+Math.sin(heading)*3,p[1],p[2]+Math.cos(heading)*3];
      if (arenaInfo && dist(point,arenaInfo.center)>arenaInfo.radius-2) continue;
      if (!walkSegment(p,point)) continue;
      // 检查整段近战净距，不能仅凭终点较远就穿过另一名敌人。
      let clearance = Infinity;
      for (let i=1;i<=6;i++) {
        const q=[p[0]+(point[0]-p[0])*i/6,p[1],p[2]+(point[2]-p[2])*i/6];
        clearance=Math.min(clearance,...enemies.map(e=>dist(q,e.position)));
      }
      const endClearance=Math.min(...enemies.map(e=>dist(point,e.position)));
      const targetDistance=dist(point,target.e.position);
      const score=4*Math.cos(norm(heading-preferred)) + .6*Math.min(1.5,endClearance-nearest.d)
        - 4*Math.max(0,targetDistance-CFG.swordRange);
      candidates.push({heading,score,clearance,endClearance});
    }
    const safe=candidates.filter(c=>c.clearance>=nearest.d-.15 && c.endClearance>nearest.d+.1);
    const choices=safe.length ? safe : candidates;
    choices.sort((a,b)=>safe.length ? b.score-a.score : b.clearance-a.clearance || b.score-a.score);
    return choices[0]?.heading ?? fallback;
  }

  // 敌人群体的中心（要围着它们转圈，就用这个当圆心）
  function groupCenter(s) {
    const es = (s.enemies || []).filter((e) => !e.hidden && e.hp > 0);
    if (!es.length) return null;
    let cx = 0, cz = 0;
    for (const e of es) { cx += e.position[0]; cz += e.position[2]; }
    return [cx / es.length, 0, cz / es.length];
  }

  // 始终保留切向速度；径向修正只让轨迹缓慢收拢或外扩。
  // bearing 指向圆心，因此大于 90° 才是向外，小于 90° 是向内。
  function steeringTarget(s, me, now) {
    const list = threats(s, me, now);
    const near = nearestEnemy(s, me);
    const group = groupCenter(s);
    // 火莲/困阵由独立特效计时，锁定脚下而非追踪；持续走位点杀直到阵法消失。
    // 已锁定目标作为圆心；尚未锁定时，竹林沿场心外圈等待选择目标。
    const bamboo = s.arena === 'bamboo' && arenaInfo;
    const selected = (s.enemies || []).find(e=>e.id===s.target && !e.hidden && !e.returning && e.hp>0);
    const c = selected ? selected.position : group && (bamboo ? arenaInfo.center : group);
    if (!c) return { want: null, roll: false, dodging: false, center: null, radius: 0 };
    const enemies = (s.enemies || []).filter(e => !e.hidden && e.hp > 0);
    const spread = enemies.reduce((r, e) => Math.max(r, dist(c, e.position)), 0);
    const boss = enemies.some(isBoss);
    const radius = selected ? (isBoss(selected) ? 18 : CFG.orbitRadius) : bamboo ? Math.min(18, arenaSafeRadius() - 1) : boss ? 18 : enemies.length ? Math.max(CFG.orbitRadius, Math.min(12, spread + 4)) : CFG.centerRadius;
    const d = dist(me, c);
    const radialLimit = d > 26 ? 1.5 : .65;
    const radial = Math.max(-radialLimit, Math.min(radialLimit, (d - radius) / Math.max(2, CFG.orbitBand)));
    let want = bearingTo(me, c) + state.orbitDir * Math.atan2(1, radial);
    let roll = false, threat = null;
    if (list.length) {
      const t = list[0];
      // 多名远程交替出手时保持同一外圈方向，避免逐个横移指令把路线拉成穿场折线。
      if (!bamboo || !['bossQi','bowShot'].includes(t.move)) {
        want = t.want ?? bearingTo(me, t.e.position) + state.orbitDir * Math.PI / 2;
      }
      // 近身的最终方向交给下方横移避险选择；冲刺、剑气与跃斩保留各自预警方向。
      if (isBoss(t.e) && t.dist < 8 && !t.hazard && !['bossDash', 'bossQi', 'bossLeap'].includes(t.move)) {
        want = bearingTo(me, t.e.position) + state.orbitDir * Math.PI * .85;
      }
      // 弓箭与普通近战侧移即可，只有重招/未知近身预警用翻滚。
      roll = t.critical && !t.reaction.responded; threat = t;
    } else if (near && near.d < CFG.keepAway) {
      want = bearingTo(me, near.e.position) + state.orbitDir * Math.PI * 0.65;
      // 低血量且被多人贴住时才使用紧急脱身。
      roll = s.hp / Math.max(1, s.hpMax) < .3 && enemies.filter(e => dist(me, e.position) < 3.5).length >= 2 && now - state.lastRollAt > 1800;
    }
    const retreat = !!near && near.d < CFG.retreatDistance;
    if (retreat && !threat?.hazard) want = retreatHeading(s,me,want);
    return { want, roll, threat, retreat, mobile:retreat || (s.treasureFx || []).includes('trap') || list.some(t=>t.mobile), critical: retreat || !!threat?.critical, dodging: now < state.evadeUntil, center: c, radius };
  }

  // 只根据实际位移判断受阻；点杀施法与翻滚也可能移动，不能仅因姿势重置计时。
  function combatHeading(s, me, want, now) {
    if (s.arena !== 'bamboo' || !arenaInfo || !Number.isFinite(want)) return want;
    if (!combatEscape || s.action && !['dodge','cast'].includes(s.action.kind) || s.paused) {
      combatEscape = { anchor:me.slice(), at:now, until:0, heading:null, failures:[] };
      return want;
    }
    if (now < combatEscape.until) return combatEscape.heading;
    if (now - combatEscape.at < 1200) return want;
    const moved = dist(me,combatEscape.anchor);
    combatEscape.anchor = me.slice(); combatEscape.at = now;
    if (moved >= 1) { combatEscape.failures = []; return want; }
    combatEscape.failures.push(combatEscape.heading ?? want);
    if (combatEscape.failures.length > 5) combatEscape.failures.shift();
    const candidates = [];
    for (const offset of [Math.PI/2,-Math.PI/2,Math.PI/4,-Math.PI/4,Math.PI]) {
      const heading = keepInside(me,want+offset);
      if (combatEscape.failures.some(h=>Math.abs(norm(h-heading))<.45)) continue;
      const point = [me[0]+Math.sin(heading)*3,0,me[2]+Math.cos(heading)*3];
      if (dist(point,arenaInfo.center) >= arenaInfo.radius-1.5) continue;
      const danger = nearestEnemy(s,point)?.d ?? 20;
      candidates.push({heading,score:Math.min(12,danger)-Math.abs(offset)*.15});
    }
    candidates.sort((a,b)=>b.score-a.score);
    if (!candidates.length) { combatEscape.failures=[]; return want; }
    combatEscape.heading=candidates[0].heading; combatEscape.until=now+1200;
    log('局部受阻，侧移脱身');
    return combatEscape.heading;
  }

  function steerCombat(s, me, want, now, retreat = false) {
    const heading = retreat ? want : keepInside(me, combatHeading(s, me, want, now));
    if (s.arena !== 'bamboo' || !fieldEnvironment) return steer(heading);
    const world = readWorld(), p = world?.actorPosition;
    if (!p || !Number.isFinite(heading)) { releaseKeys(); return null; }
    const target = [p[0]+Math.sin(heading)*3,p[1],p[2]+Math.cos(heading)*3];
    const hazards = groundHazards();
    const input = chooseWalkInput(world,target,fieldEnvironment,[],{center:arenaInfo.center,radius:arenaInfo.radius-2},hazards.length ? hazards : null);
    if (!input) { releaseKeys(); return null; }
    setKeys(input.keys);
    return {keys:input.keys.join(''),heading:input.heading,basis:input.basis,want:heading};
  }

  // ============ 7. 输出：飞剑点杀风筝 ============
  function dodgeThreat(s, step, now) {
    if (!step.roll || s.action?.kind === 'dodge' || (s.stamina || 0) < 25 || s.cooldown?.dodge !== 0 || now - state.lastRollAt < 700) return false;
    if (!act('dodge')) return false;
    if (step.threat) step.threat.reaction.responded = true;
    state.lastRollAt = now; state.evadeUntil = now + 180;
    state.stats.rolls++; state.stats.dodges++;
    return true;
  }
  function combatAction(id, counter, now) {
    if (now < (state.skillRetryAt[id] || 0)) return false;
    if (!act(id)) { state.skillRetryAt[id] = now + 400; return false; }
    state.skillRetryAt[id] = 0;
    if (counter) state.stats[counter]++;
    return true;
  }
  function castUltimate(s, near, now) {
    if (s.gcdLeft > 0 || s.casting) return false;
    if (!near || near.d >= 22 || !(s.momentum >= 100) || s.empowered ||
      s.airborne || s.paused || (s.action && s.action.kind !== 'cast')) return false;
    if (!combatAction('ult', 'ultimates', now)) return false;
    if (/^大庚剑阵/.test(s.ultimate || '')) state.stats.geng++;
    state.lastAct = 'T · ' + (s.ultimate || '大招'); log('输出 ' + state.lastAct);
    // 大庚起手自带无敌，不再为了取消后摇额外消耗一次翻滚。
    return true;
  }

  //   只用「冷却制」技能，不用 guard / 九剑绞杀这类开关技能
  //   —— 它们会停止灵力回复，反而让飞剑打不出去
  function movableTreasure(slot, combat) {
    if (!Number.isFinite(combat.gcdLeft) || combat.empowered) return true;
    const rules=window.QingxiTreasureRules;
    const rule=rules?.skills?.[slot.skill] || rules?.templates?.[slot.category];
    return !!rule && (rule.cast || 0)===0;
  }
  function attack(s, near, now, critical = false, mustMove = false) {
    const cd = s.cooldown || {};
    const mana = s.mana || 0;
    const hpRatio = s.hp / Math.max(1, s.hpMax);
    const bossFight = (s.enemies || []).some(e => !e.hidden && e.hp > 0 && isBoss(e));

    if (hpRatio < (bossFight ? CFG.bossPotionHpBelow : CFG.potionHpBelow) && cd.hpPotion === 0 && (s.potions || {}).hp > 0) {
      if (act('hpPotion')) { state.stats.potions++; return 'hpPotion'; }
    }
    const manaCost = s.kit?.find(slot => (slot.skill === 'qingzhu' || !slot.skill && slot.category === 'sword') && !slot.locked)?.cost;
    const pierceMana = Number.isFinite(manaCost) ? manaCost : CFG.pierceMana;
    const muReserve=yufengManaReserve(s,near?.e);
    if (mana < Math.max(CFG.potionManaBelow, pierceMana+muReserve) && cd.manaPotion === 0 &&
      ((s.potions || {}).mana > 0 || (s.potions || {}).carry > 0)) {
      if (act('manaPotion')) { state.stats.potions++; return 'manaPotion'; }
    }
    if (!near || s.gcdLeft > 0 || s.casting) return 'none';
    // 移动中仍允许普通点杀命令；只因远程预警持续而停火会造成输出饥饿。
    // 首领追近时也不能为法宝/术法停步；原生飞剑仍可继续输出。
    const me = myPos();
    const bossClose = bossFight && (s.enemies || []).some(e => !e.hidden && e.hp > 0 && isBoss(e) && dist(me, e.position) < 8);
    const bambooClose = s.arena === 'bamboo' && (s.enemies || []).some(e => !e.hidden && !e.returning && e.hp > 0 && dist(me, e.position) < 10);
    const impactNow = critical;
    critical ||= mustMove || bossClose || bambooClose;
    // 锁定目标：飞剑点杀要靠它选目标，只在没目标时锁一次
    if (!s.target && now - (state.lastLockAt || 0) > 1500) {
      state.lastLockAt = now;
      if (act('lock')) return 'lock';
    }
    // 飞剑点杀：主力输出
    if (cd.pierce === 0 && mana >= pierceMana+muReserve && near.d < 32) {
      if (combatAction('pierce', 'pierces', now)) return 'pierce';
    }
    // 穆上师需要主动法宝打断登车；点杀、分化及通用兜底不能花掉这份灵力。
    if (muReserve>0) return 'none';
    // 原生瞬发法宝的上身动作不锁脚；没有飞剑时也能在冰晶/箭雨期间持续输出。
    if (mustMove && !impactNow && !bossClose && (!s.action || s.action.kind==='cast')) {
      const slot=(s.kit || []).find(k=>{
        const rule=window.QingxiTreasureRules?.skills?.[k.skill] || window.QingxiTreasureRules?.templates?.[k.category];
        return !k.locked && (k.cooldown??0)===0 && Number.isFinite(k.cost) && mana>=k.cost &&
          rule && (rule.cast||0)===0 && rule.damage>0 && rule.range>near.d && now>=(state.skillRetryAt['slot'+k.slot]||0);
      });
      if(slot && combatAction('slot'+slot.slot,'treasures',now))return slot.name;
    }
    const cluster = (s.enemies || []).filter(e => !e.hidden && !e.returning && e.hp > 0 && dist(e.position, near.e.position) <= 4).length;
    // 已装备的独立法宝可以填补飞剑回旋空档；避险时仍允许原生可用的点杀。
    if (!critical && near.d < 14 && cluster >= 2 && cd.giant === 0 && mana >= pierceMana + 28) {
      if (combatAction('swords', 'giants', now)) return 'H · 巨剑术';
    }
    if (!critical && near.d < 24) {
      // 第五、六法宝首次使用前没有冷却键；原生按钮把缺省值视为可用。
      const slot = (s.kit || []).find(k => !k.locked && (k.cooldown ?? 0) === 0 && Number.isFinite(k.cost) && mana >= k.cost + pierceMana &&
        (k.skill === 'iron_sword' || ['chisi','yinyang_swords'].includes(k.skill) || ['suppress', 'flame', 'ring', 'light'].includes(k.category)) && movableTreasure(k,s) && now >= (state.skillRetryAt['slot' + k.slot] || 0));
      if (slot && combatAction('slot' + slot.slot, 'treasures', now)) return slot.name;
      // 游戏首次施放后才建立 artN 冷却键，缺省表示尚未进入冷却。
      const artIndex = (s.arts || []).indexOf(s.art), artReady = s.art === '火弹' ? cd.spell === 0 : artIndex >= 0 && (cd['art' + artIndex] ?? 0) === 0;
      const harmful = ['星陨', '烈焰焚天', '奔雷咒', '玄冰刺', '元神出窍', '灵木缠绕', '灵兽突袭', '煞气侵体', '神识刺', '魅惑', '火弹'].includes(s.art);
      if (harmful && (!Number.isFinite(s.gcdLeft) || s.empowered || window.QingxiTreasureRules?.arts?.[s.art]?.cast===0) && artReady && mana >= pierceMana + 26 && (!s.action || s.action.kind === 'cast')) {
        if (combatAction('art', 'arts', now)) return 'V · ' + s.art;
      }
    }
    // 剑光分化（群伤，冷却 6 秒）
    if (!mustMove && s.azure && cd.fan === 0 && mana >= CFG.fanMana && near.d < 30) {
      if (combatAction('fan', 'fans', now)) return 'fan';
    }
    return 'none';
  }

  // ============ 8. 主循环 ============
  function recordDamage(s, now) {
    if (Number.isFinite(state.lastHp) && s.hp < state.lastHp) {
      const me = myPos();
      state.damage.push({ time: now, hpBefore: state.lastHp, hp: s.hp, hpMax: s.hpMax,
        mana: s.mana, stamina: s.stamina, action: s.action?.kind || null,
        keys: [...state.heldKeys].join(''), position: me, hpPotions: s.potions?.hp ?? null,
        hpPotionCooldown: s.cooldown?.hpPotion ?? null,
        // 这是受击时的敌人快照，不能把最近敌人直接认定为伤害来源。
        arena:s.arena, wave:s.wave?.index, phase:state.phase, treasureFx:(s.treasureFx || []).slice(), shots:(s.shots || []).slice(),
        enemies: (s.enemies || []).filter(e => !e.hidden && e.hp > 0)
          .sort((a,b) => dist(me,a.position)-dist(me,b.position)).slice(0,3)
          .map(e => ({ id:e.id, name:e.name, role:e.role, phase:e.phase, move:e.move, time:e.time, state:e.state, telegraph:e.telegraph, distance:dist(me,e.position),
            ...(e.role==='yufeng_mu'&&e.behavior?{behavior:{time:e.behavior.time,mode:e.behavior.mode,anim:e.behavior.anim,
              counts:{...e.behavior.counts},vfx:{...e.behavior.vfx}},dodge:state.story?.muThreat?.cast?{
                type:state.story.muThreat.cast.type,at:state.story.muThreat.cast.at,lockedAt:state.story.muThreat.cast.lockedAt,
                aim:state.story.muThreat.cast.aim.slice(),responded:state.story.muThreat.cast.reaction.responded}:null}:{} ) })) });
      if (state.damage.length > 20) state.damage.shift();
      log('受到伤害 ' + Math.ceil(state.lastHp - s.hp) + '，气血 ' + Math.ceil(s.hp) + '/' + s.hpMax);
    }
    state.lastHp = s.hp;
  }
  function tick() {
    if (!state.running) return;
    const now = performance.now();
    state.stats.ticks++;
    const s = readCombat();
    if(s?.state==='defeat')observeBattleFailure();
    if (state.driveOwner && (window.__rt !== state.driveOwner || window.__fc !== state.combatOwner)) {
      finishDriving('场景或战斗已切换'); return;
    }
    if (!s || !s.active || s.overworld || !s.wave ||
      (state.expectedArena && s.arena !== state.expectedArena)) {
      finishDriving('已离开当前试炼'); return;
    }
    recordDamage(s, now);
    if (s.trial?.syncError || s.trial?.anomaly) {
      finishDriving('战绩同步异常：' + (s.trial.syncError || s.trial.anomaly), s, true, true); return;
    }
    if (['victory', 'defeat'].includes(s.state)) {
      releaseKeys();
      state.settlingAt ||= now;
      state.phase = '等待战绩结算';
      if (s.trial && (s.trial.syncing > 0 || !s.trial.run)) {
        if (now - state.settlingAt > CFG.settlementTimeoutMs) finishDriving('战绩结算超时，请在游戏内核对', s, true, true);
        return;
      }
      if (state.retry.enabled && s.state === 'defeat') {
        state.retry.history.push({attempt:state.retry.attempts, version:'2.0.42',
          enemies:s.enemies.map(e=>({name:e.name,hp:e.hp,maxHp:e.maxHp,phase:e.phase})),
          stats:{...state.stats}, damage:state.damage.slice()});
        if (state.retry.history.length > 20) state.retry.history.shift();
        stopDriving();
        if (!act('retry')) { finishDriving('游戏未接受本波重试，请核对试炼状态',s,true); return; }
        state.retry.attempts++; startDriving(s.arena); return;
      }
      finishDriving(s.state === 'victory' ? '试炼通关' : '试炼失利', s);
      return;
    }
    if (s.state !== 'fighting') { finishDriving('试炼状态已变化'); return; }
    if (now - state.driveStartedAt > CFG.fightTimeoutMs && state.driveOwner) {
      finishDriving('自驾已达时限', s, true); return;
    }
    const hud = window.__rt?.mobileHudState?.();
    if (s.paused || document.hidden || hud?.blocked || document.getElementById('dwellingWorld')?.hidden) {
      releaseKeys(); combatEscape=null; state.focusRetryAt=0; state.phase = '暂停操控（游戏暂停、面板或后台）'; return;
    }
    state.phase = '试炼自驾中';
    const me = myPos();
    if (!me) { releaseKeys(); return; }

    waveHome(s, me); // 首帧记下开局位置
    // 清场后的空档是换波等待：走回开局位置后原地松键，不绕场心，也不放技能。
    if (s.wave.interlude > 0 || !(s.enemies || []).some(e => !e.hidden && e.hp > 0)) {
      state.evadeUntil = 0;
      state.gengCancelUntil = 0;
      // 只有本波确实清空（换波倒计时，或没有任何存活敌人）才移动；还有隐身敌人时原地等。
      const cleared = s.wave.interlude > 0 || !(s.enemies || []).some(e => e.hp > 0);
      if (cleared && returnToWaveHome(s, me)) { state.lastAct = '回到开局位置'; return; }
      releaseKeys();
      state.lastAct = '等待下一波';
      return;
    }

    captureArena(s, me);

    // 1) 先选择攻击目标，再围绕它横移；固定场心只用于边界限制。
    const focused = focusRanged(s,me,now);
    const step = steeringTarget(focused, me, now);
    const move = steerCombat(focused, me, step.want, now, step.retreat);
    const rolled = dodgeThreat(focused, step, now);
    if (!state.trace.length || now - state.trace[state.trace.length - 1].time >= 250) {
      state.trace.push({ time: now, position: me, center: step.center, radius: step.radius,
        arenaDistance: dist(me, arenaInfo.center), hp:s.hp, mana:s.mana, action:s.action?.kind || null,
        threat:step.threat?.move || null, rolled, ...move });
      if (state.trace.length > 1200) state.trace.shift();
    }

    // 2) 侧移不再停火，T 满气势优先；重招避险期间保留原生允许的飞剑输出。
    const near = attackTarget(focused, me);
    const ultimate = !rolled && !step.critical && !step.mobile && castUltimate(focused, near, now);
    const a = attack(ultimate || rolled ? readCombat() : focused, near, now, step.critical, step.mobile);
    if (a !== 'none') { state.lastAct = a; log('输出 ' + a); }
  }

  function log(line) {
    state.log.push(`[${(performance.now() / 1000).toFixed(1)}s] ${line}`);
    if (state.log.length > 200) state.log.splice(0, 100);
  }

  // ============ 9. 可取消的入坊 / 开战流程 ============
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  class Cancelled extends Error {}
  function checkOwner(owner) {
    if (owner !== state.generation) throw new Cancelled('已停止');
  }
  function step(message) { state.phase = message; log(message); console.log('[自驾] ' + message); }
  async function until(fn, timeoutMs, label, owner) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      checkOwner(owner);
      if (fn()) return true;
      await sleep(300);
    }
    checkOwner(owner);
    throw new Error('等待超时：' + label);
  }
  function shown(id) {
    const element = document.getElementById(id);
    return !!element && !element.hidden && !element.closest?.('[hidden]');
  }
  function clickOnce(id) {
    const button = document.getElementById(id);
    if (!button || button.disabled || !shown(id)) return false;
    button.click();
    return true;
  }
  function loadingFailure() {
    if (shown('worldLoading') && shown('worldLoadingRetry')) {
      throw new Error('青溪坊资源加载失败，请点击游戏内「重新加载」后重试');
    }
  }
  function sceneReady(w = readWorld()) {
    if (!w?.environment || shown('worldLoading')) return false;
    const env = w.environment;
    const modelReady = w.companion?.modelReady ?? window.__rt?.mobileHudState?.()?.modelReady;
    if (!modelReady) return false;
    // 新版坊市有多个场景资源，loaded 不再固定为 1。
    return env.tier === 4 && env.loading ? env.loading.ready === true : env.pending === 0;
  }
  function caveEntrance() {
    return (readWorld()?.targets||[]).find(t=>t.kind==='battle-exit' && position3(t.point) && t.point.length===3 &&
      Number.isFinite(t.radius) && t.radius>.5);
  }
  async function enterTownFromCave(owner) {
    let gate;
    await until(()=>{
      loadingFailure();const world=readWorld();
      if(world?.environment?.tier===4)return true;
      if(world?.portal?.failures)throw new Error('洞府出府山门加载失败，请重新进入洞府后重试');
      if(world?.portal?.pending>0 || Number.isFinite(world?.portal?.loaded) && world.portal.loaded<1)return false;
      gate=caveEntrance();
      return !!gate || openWorldControls() && shown('worldTownEntry') && !document.getElementById('worldTownEntry').disabled;
    },30000,'洞府山门或青溪坊入口菜单',owner);
    if(readWorld()?.environment?.tier===4)return;
    if(!gate){
      // 无实体入口目录的旧页面仍支持操作菜单直入；只点一次。
      try {
        if(!clickOnce('worldTownEntry'))throw new Error('青溪坊入口暂不可用，请检查登录状态后重试');
      } finally { closeWorldControls(); }
      return;
    }
    if(!fieldEnvironment?.canWalk || !fieldEnvironment?.groundHeight)throw new Error('未捕获洞府道路碰撞，请启用新版脚本后重新进入洞府');
    const rt=window.__rt,previousStory=state.story,camera=rt.cameraFollowSettings?.()?.enabled;
    state.story={runtime:rt,quests:window.__quests,deadline:Date.now()+180000,options:{}};
    try {
      step('走向洞府出府山门');rt.setControl('companion');rt.setCameraFollowEnabled?.(false);
      clickOnce('worldSheetClose');canvas()?.focus?.({preventScroll:true});
      await walkTo(gate.point,gate.radius-.35,owner,{flight:false,defend:false});
      storyCheck(owner);releaseKeys();
      // 指定山门，不能让最近的侍妾/陈设抢走 E 交互。
      const selected=rt.selectTarget?.(gate.id)===true || readWorld()?.interaction?.id===gate.id && readWorld()?.interaction?.ready;
      if(!selected)throw new Error('尚未进入洞府山门交互范围，请靠近入口后重试');
      if(typeof rt.interact!=='function')throw new Error('洞府山门原生交互接口不可用，请刷新游戏后重试');
      rt.interact();
      let button;
      await until(()=>button=uiButton('#worldSheetActions button',text=>/^前往[清青]溪坊$/.test(text)),10000,'山门入坊选项；请检查登录状态',owner);
      checkOwner(owner);step('洞府山门 · 前往青溪坊');button.click();
      // 同一菜单的「进入试炼」绝不作为入坊兜底。
    } finally {
      if(owner===state.generation){
        releaseKeys();state.navigation=null;state.story=previousStory;
        if(typeof camera==='boolean' && window.__rt===rt)rt.setCameraFollowEnabled?.(camera);
      }
    }
  }
  async function prepareTown(owner) {
    step('进入青溪坊');
    if (!shown('dwellingWorld')) {
      await until(() => clickOnce('dwellingEnter'), 30000, '入府漫游按钮', owner);
      await until(() => shown('dwellingWorld'), 60000, '洞府漫游面板', owner);
    }
    await until(() => !!window.__rt, 240000, '场景运行时；请确保脚本在刷新前启用', owner);
    applyLowQuality('场景加载');
    await until(() => { loadingFailure(); return sceneReady(); }, 420000, '场景与人物', owner);
    closeWorldControls();
    if (readWorld()?.environment?.tier !== 4) {
      // 已在坊市绝不点「返回洞府」；洞府落点则优先走真实出府山门。
      await enterTownFromCave(owner);
      await until(() => {
        loadingFailure();
        return readWorld()?.environment?.tier === 4 && sceneReady();
      }, 420000, '青溪坊场景', owner);
    }
    checkOwner(owner);
    window.__rt.setControl('companion');
    step('青溪坊已就绪');
  }
  async function workflow(work, {preserveResult=false,returnOnError=true,preserveError=false} = {}) {
    if (state.busy || state.running) return '已有操作进行中，请先停止';
    const owner = ++state.generation;
    state.busy = true; if (!preserveError) state.error = ''; if (!preserveResult) state.result = null; state.navigation = null;
    try { return await work(owner); }
    catch (error) {
      if (error instanceof Cancelled || owner !== state.generation) return '已停止';
      reportFailure(error);
      // 收尾只校验取消代次，不使用已过期的任务时限或已切换的主线场景。
      state.error = error.message;
      let homeMessage = '';
      if (returnOnError && window.__rt && readWorld() && (state.home?.owner !== owner || state.home.verified)) {
        try { await finishInCave(owner); homeMessage = '；已返回洞府'; }
        catch (homeError) {
          if (homeError instanceof Cancelled || owner !== state.generation) return '已停止';
          returnJournalProtection();
          homeMessage = '；返回洞府未确认：' + homeError.message;
          log(homeMessage); reportFailure(homeError);
        }
      }
      if (!homeMessage && returnOnError && state.home?.owner === owner && !state.home.verified)
        homeMessage = '；返回洞府未确认：' + state.home.error;
      checkOwner(owner); step(error.message + homeMessage); return error.message + homeMessage;
    } finally { if (owner === state.generation) state.busy = false; }
  }
  async function autoEnterFromEntry() {
    if (!rewardBridge || inherited || window.__dwellingAutoTownStarted || state.busy || state.running || shown('dwellingWorld')) return;
    window.__dwellingAutoTownStarted = true;
    return workflow(async owner => {
      step('等待角色资料，随后自动进入青溪坊');
      await until(() => {
        const refresh = document.getElementById('refreshBtn');
        const subtitle = document.getElementById('subtitle')?.textContent || '';
        return refresh && !refresh.disabled && /^@.+ · /.test(subtitle);
      }, 90000, '入口角色资料；请检查登录状态后手动进入青溪坊', owner);
      if (typeof window.ownWorldIdentity !== 'function' || String(window.ownWorldIdentity()) !== rewardBridge.player_id) {
        throw new Error('入口角色与当前身份不一致，请从控制台重新打开洞府');
      }
      await prepareTown(owner);
      return '已自动进入青溪坊';
    });
  }
  function restoreCamera() {
    const old = state.cameraBefore;
    state.cameraBefore = null;
    if (old && window.__rt === old.runtime) old.runtime.setCameraFollowEnabled?.(old.enabled);
  }
  function stopDriving() {
    state.running = false;
    clearInterval(state.timer); state.timer = null;
    releaseKeys(); restoreCamera();
    state.gengCancelUntil = 0; state.evadeUntil = 0;
  }
  function finishDriving(message, combat, error = false, settlement = false) {
    if(error)reportFailure(message,combat || readCombat());
    if (settlement) markSkippedSettlement(combat || readCombat());
    state.retry.enabled = false;
    stopDriving();
    // 同步异常和超时可能发生在活跃战斗中，松键后也要暂停游戏，避免站着挨打。
    const current = combat || readCombat();
    if (error && current?.active && !current.overworld && current.state === 'fighting' && !current.paused) {
      message += act('pause') ? '（游戏已暂停）' : '（请手动暂停游戏）';
    }
    if (error) state.error = message;
    if (combat) state.result = {
      arena: combat.arena, outcome: combat.state, results: combat.results || null,
      runId: combat.trial?.run?.id || null,
      rewarded: combat.trial?.run?.rewarded ?? null,
      tier: combat.trial?.run?.tier ?? combat.trial?.tier?.run ?? null,
      grade: combat.trial?.grade || null, rewards: combat.trial?.rewards || [],
      syncing: combat.trial?.syncing || 0, syncError: combat.trial?.syncError || '',
      anomaly: combat.trial?.anomaly || '', code:settlement ? 'settlement_anomaly' : null, verified: !error && combat.state === 'victory' && !!combat.trial?.run && !combat.trial?.syncing,
    };
    step(message);
    if (!state.story && window.__rt && readWorld()) {
      const owner=state.generation, runtime=window.__rt, controller=window.__fc;
      setTimeout(()=>{ if (owner===state.generation && window.__rt===runtime && window.__fc===controller && !state.busy && !state.running) void returnCave({preserveError:true}); },0);
    }
  }
  function startDriving(arena) {
    if (window.__fc) skippedCombatSettlements.delete(window.__fc);
    releaseKeys(); state.navigation=null;
    arenaInfo = null; state.evadeUntil = 0; state.gengCancelUntil = 0;
    // 新开的一局重新记录开局位置；同一局的本波重试、暂停后恢复沿用原记录。
    const opening = readCombat()?.wave;
    state.waveHomePath = null;
    if (state.waveHome && (state.waveHome.combat!==window.__fc || state.waveHome.arena!==arena || ((opening?.index || 0)===0 && !(opening?.time>5)))) state.waveHome = null;
    state.trace = []; state.damage = []; state.lastHp = null; lastReact.clear(); qiFlights.clear(); arrowFlights.clear(); combatEscape=null; state.focusRetryAt=0;
    state.lastRollAt = -Infinity; state.skillRetryAt = {};
    for (const key of Object.keys(state.stats)) state.stats[key] = 0;
    state.result = null; state.error = ''; state.expectedArena = arena;
    state.driveOwner = window.__rt; state.combatOwner = window.__fc;
    state.driveStartedAt = performance.now(); state.settlingAt = 0;
    const enabled = window.__rt.cameraFollowSettings?.()?.enabled;
    if (typeof enabled === 'boolean') state.cameraBefore = { runtime: window.__rt, enabled };
    window.__rt.setCameraFollowEnabled?.(false);
    state.running = true; state.orbitDir = CFG.orbitDir;
    clearInterval(state.timer);
    state.timer = setInterval(() => {
      try { tick(); } catch (error) { finishDriving('自驾异常：' + error.message, null, true); }
    }, CFG.tickMs);
    step('试炼自驾中');
  }
  const TRIALS = { longwind: '长风原 · 麦田试炼', bamboo: '听竹坳 · 竹林试炼' };
  // 每日只选一重，但仍完整打完本场四波。通过原生降重同时改变敌势与开局参数，
  // 不能只改请求里的 tier（那会与客户端实际敌势不一致）。首通会清空原生选择，所以每局重选。
  function selectFirstTrialTier(combat) {
    const snapshot = combat?.stats?.();
    if (!snapshot || snapshot.overworld || !Object.hasOwn(TRIALS, snapshot.arena)) return;
    if (snapshot.active && ['fighting','defeat'].includes(snapshot.state)) return; // 已开局不改重数，失败仍原生本波重试。
    let chosen = snapshot.trial?.tier?.chosen;
    if (!Number.isInteger(chosen) || chosen < 1 || chosen > 1000) throw new Error('未确认试炼重数，暂不开始每日试炼');
    while (chosen > 1) {
      if (combat.command?.('tierDown') !== true) throw new Error('游戏未接受选择第一层（一重），暂不开始每日试炼');
      const next = combat.stats?.()?.trial?.tier?.chosen;
      if (next !== chosen - 1) throw new Error('未确认试炼降重，暂不开始每日试炼');
      chosen = next;
    }
  }
  class TrialPractice extends Error {
    constructor(arena, message) { super(message); this.arena=arena; this.code='trial_no_rewards'; }
  }
  function trialEntryPrompt(combat = window.__fc) {
    // 只读当前战斗 HUD 的本局提示，不把旧战绩、助手日志或“剩余 0 次”当成本局无奖。
    return combat?.mobileHudView?.()?.toast?.text || document.querySelector('.field-combat-touch [data-notice]')?.textContent || '';
  }
  function holdTrialEntry(combat) {
    const live=combat?.stats?.();
    if (!live?.active || live.overworld || live.state!=='fighting' || !Object.hasOwn(TRIALS,live.arena) || live.trial?.run?.rewarded===true) return;
    if (live.paused) return;
    if (combat.command?.('pause')!==true || !combat.stats?.()?.paused) throw new Error('无法暂停试炼等待有奖提示，请先手动暂停');
    state.trialEntryHold={runtime:combat,arena:live.arena,owner:state.generation};
    releaseKeys();
  }
  async function exitPracticeTrial(arena, owner, reason) {
    checkOwner(owner);
    const live=readCombat(), runtime=window.__fc;
    if (!live?.active || live.overworld || live.arena!==arena) throw new Error('无奖退出前场景已切换，未操作新战斗');
    state.retry.enabled=false; stopDriving();
    if (live.state==='fighting' && !live.paused) holdTrialEntry(runtime);
    state.result={arena,runId:live.trial?.run?.id || null,tier:live.trial?.run?.tier ?? live.trial?.tier?.run ?? null,
      outcome:'practice',code:'trial_no_rewards',rewarded:false,verified:false,reason};
    step(reason+'；退出无奖试炼');
    if (!act('stop')) throw new Error('游戏未接受退出无奖试炼，已保留暂停保护');
    await until(()=>{
      if (window.__fc!==runtime || readCombat()?.arena!==arena) throw new Error('退出无奖试炼时场景已切换');
      return !readCombat()?.active;
    },10000,'退出无奖试炼',owner);
    state.trialEntryHold=null;
  }
  async function verifyTrialEntry(arena, owner, previousPrompt = '', resume = true) {
    checkOwner(owner);
    const runtime=window.__fc;
    const receipt={arena,status:'checking',runId:null,reason:'',prompt:'',at:Date.now()};
    state.trialEntry=receipt;
    step('查看本局试炼提示：'+TRIALS[arena]);
    try {
      await until(()=>{
        const live=readCombat();
        if (window.__fc!==runtime || !live?.active || live.overworld || live.arena!==arena) throw new Error('查看试炼提示期间场景已切换');
        if (live.trial?.syncError || live.trial?.anomaly)
          throw new SettlementError(arena,'试炼入场回执异常：'+(live.trial.syncError || live.trial.anomaly),live);
        const run=live.trial?.run, prompt=trialEntryPrompt(runtime);
        receipt.prompt=prompt; receipt.runId=run?.id || null;
        if (run?.arena && run.arena!==arena) throw new SettlementError(arena,'试炼入场返回另一场地的 run',live);
        // run.rewarded 是原生“有奖 / 本局为练习，不发奖励”提示的依据，优先于次数快照。
        if (run?.id && typeof run.rewarded==='boolean') {
          receipt.status=run.rewarded?'rewarded':'no_rewards';
          receipt.reason=run.rewarded?'本局已确认有奖':'游戏提示本局为练习，不发奖励';
          return true;
        }
        if (prompt && prompt!==previousPrompt && /本局[^。；]*?(?:练习[^。；]*?不发奖励|无奖|无奖励)/.test(prompt)) {
          receipt.status='no_rewards'; receipt.reason=prompt; return true;
        }
        holdTrialEntry(runtime);
        return false;
      },CFG.trialEntryTimeoutMs,'本局有奖/无奖提示',owner);
      checkOwner(owner);
      if (receipt.status==='no_rewards') {
        await exitPracticeTrial(arena,owner,receipt.reason);
        throw new TrialPractice(arena,receipt.reason+'，已退出并停止本轮');
      }
      const day=readCombat()?.trial?.status?.day;
      if (state.batch?.status==='running' && /^\d{4}-\d{2}-\d{2}$/.test(day)) batchDay(day);
      const held=state.trialEntryHold,live=readCombat();
      if (resume && held?.runtime===runtime && held.arena===arena && held.owner===owner) {
        if (live?.paused && live.state==='fighting' && (!act('pause') || readCombat()?.paused)) throw new Error('游戏未接受恢复有奖试炼');
        state.trialEntryHold=null;
      }
      step(receipt.reason+'：'+TRIALS[arena]);
      return receipt;
    } catch (error) {
      if (error instanceof TrialPractice) throw error;
      receipt.status=error instanceof Cancelled || owner!==state.generation?'cancelled':'unconfirmed';
      receipt.reason=error.message;
      if (error instanceof Cancelled || owner!==state.generation || error instanceof SettlementError) throw error;
      throw new SettlementError(arena,'未确认本局是否有奖励，停止自动战斗：'+error.message,readCombat());
    }
  }
  function hookDailyTrialStart(combat) {
    if (!combat || typeof combat.start !== 'function') return;
    const key = Symbol.for('dwelling-autobot.daily-trial-start');
    const select = config => {
      if (window.__fc === combat && state.busy && state.batch?.mode === 'trials' && state.batch.status === 'running' && !config?.questMode)
        selectFirstTrialTier(combat);
    };
    // 热更新只替换选择逻辑，不层层叠加开局包装，也不接管用户手动试炼。
    const hold = () => {
      // 开局已被原生接受，暂停失败不能抛进游戏自己的调用栈；入场检查会重试并在流程内报错。
      try { if (window.__fc===combat && state.busy && state.batch?.mode==='trials' && state.batch.status==='running') holdTrialEntry(combat); }
      catch (error) { log('开局后暂停保护未生效，将在入场检查时重试：'+error.message); }
    };
    if (combat[key]) { combat[key].select=select; combat[key].hold=hold; return; }
    const hook = {select,hold};
    Object.defineProperty(combat, key, {value:hook});
    combat.start = new Proxy(combat.start, {apply(target, receiver, args) {
      hook.select(args[0]); const result=Reflect.apply(target,receiver,args);
      if (result===true && !args[0]?.questMode) hook.hold(); return result;
    }});
    if (typeof combat.command === 'function') combat.command = new Proxy(combat.command, {apply(target, receiver, args) {
      if (['start','restart'].includes(args[0])) hook.select();
      const result=Reflect.apply(target,receiver,args);
      if (result===true && ['start','restart'].includes(args[0])) hook.hold(); return result;
    }});
  }
  async function beginTrial(arena, owner) {
    const previousPrompt=trialEntryPrompt();
    await prepareTown(owner);
    checkOwner(owner);
    const combat = readCombat();
    if (combat?.arena === arena && (combat.trial?.syncError || combat.trial?.anomaly))
      throw new SettlementError(arena, '战绩同步异常：' + (combat.trial.syncError || combat.trial.anomaly), combat);
    if (combat?.active && !combat.overworld && combat.state === 'fighting') {
      if (combat.arena !== arena) throw new Error('另一场试炼正在进行，请先在游戏内结束');
      await verifyTrialEntry(arena,owner,previousPrompt);
      startDriving(arena); return '已接管当前试炼';
    }
    if (combat?.trial?.syncing > 0) throw new Error('上一场战绩仍在同步，请稍后再开始');
    if (combat?.active && !combat.overworld && combat.arena===arena && combat.state==='defeat') {
      await verifyTrialEntry(arena,owner,previousPrompt);
      if (!act('retry')) throw new Error('游戏未接受本波重试');
      const retried=readCombat();
      if (!retried?.active || retried.overworld || retried.arena!==arena || retried.state!=='fighting') throw new Error('未确认本波重试开战');
      startDriving(arena); return '已重试当前波';
    }
    const rt = window.__rt;
    if (typeof rt.startTownCombat !== 'function') throw new Error('当前页面不支持试炼入口');
    const profile = rt.mobileHudState?.()?.profile;
    if (profile && !profile.swords) throw new Error('当前角色没有飞剑，暂不支持飞剑自驾');
    step('准备' + TRIALS[arena] + '（等待人物与动作）');
    // startTownCombat 自己负责切换对应试炼场景；不要先额外传送一次。
    await until(() => {
      loadingFailure();
      if (window.__rt !== rt) throw new Error('场景已切换，请重新启动');
      if (document.hidden) return false;
      const s = readCombat();
      if (s?.active && !s.overworld && s.state === 'fighting') {
        if (s.arena !== arena) throw new Error('另一场试炼已开始');
        return true;
      }
      return rt.startTownCombat(arena) === true;
    }, 420000, '开始试炼', owner);
    checkOwner(owner);
    const started = readCombat();
    if (!started?.active || started.overworld || started.state !== 'fighting' || started.arena !== arena) {
      throw new Error('未确认试炼开战，请查看游戏提示');
    }
    await verifyTrialEntry(arena,owner,previousPrompt);
    applyLowQuality('开战'); startDriving(arena);
    return '已开始';
  }
  async function startTrial(arena = 'longwind') {
    if (!Object.hasOwn(TRIALS, arena)) return '仅支持 longwind（麦田）或 bamboo（竹林）';
    return workflow(async owner => {
      state.retry.enabled = false;
      try { return await beginTrial(arena,owner); }
      catch (error) {
        if (!(error instanceof TrialPractice)) throw error;
        await finishInCave(owner); step(error.message+'；已返回洞府'); return error.message;
      }
    });
  }

  async function checkedTrialControl(arena, retry = false) {
    return workflow(async owner=>{
      try {
        await verifyTrialEntry(arena,owner,trialEntryPrompt());
        const live=readCombat();
        if (!retry && live?.state!=='fighting') return '当前没有进行中的试炼';
        if (retry) {
          if (live?.state==='defeat' && !act('retry')) throw new Error('游戏未接受本波重试');
          state.retry={enabled:true,attempts:1,history:[]};
        }
        startDriving(arena);
        return retry?'连续重试已开启':'已恢复';
      } catch (error) {
        if (!(error instanceof TrialPractice)) throw error;
        await finishInCave(owner); step(error.message+'；已返回洞府'); return error.message;
      }
    });
  }

  // 主线目录从页面提供的版本化地址读取，进度只读游戏控制器的服务端快照。
  // 不保存认证参数，不自行上报任务完成或伪造战报。
  async function loadQuestCatalog() {
    if (questCatalog) return questCatalog;
    if (questCatalogPending) return questCatalogPending;
    const source = questCatalogSource;
    if (!source?.catalogUrl) throw new Error('未捕获任务控制器，请启用脚本后刷新页面');
    const json = async url => {
      const response = await fetch(url, { cache: 'no-store' });
      if (!response.ok) throw new Error('任务目录加载失败：HTTP ' + response.status);
      return response.json();
    };
    const pending = (async () => {
      const manifest = await json(source.catalogUrl);
      const catalogs = Array.isArray(manifest.catalogs)
        ? await Promise.all(manifest.catalogs.map(async entry => ({
          region: entry.region, data: await json(source.catalogPart ? source.catalogPart(entry) : entry.path),
        }))) : [{ region: 'yue', data: manifest }];
      const merged = { quests: [], npcs: [] };
      for (const { region, data } of catalogs) for (const key of ['quests', 'npcs']) {
        for (const row of data[key] || []) merged[key].push({ ...row, region: row.region || region });
      }
      if (source !== questCatalogSource) throw new Cancelled('任务场景已切换');
      questCatalog = merged;
      return merged;
    })();
    questCatalogPending = pending;
    try { return await pending; } finally { if (questCatalogPending === pending) questCatalogPending = null; }
  }
  function taskKinds() { return [...new Set((questCatalog?.quests || []).map(q => q.kind || '未分类'))]; }
  function matchesQuestKind(actual, selected) {
    return !selected || selected === '全部任务' || (selected === '全部支线' ? actual !== '主线' : actual === selected);
  }
  function prerequisiteText(row) {
    return row.prerequisites.map(id => {
      const q = storyQuest(id), status = storySlot(id)?.status || 'locked';
      return (q?.title || id) + '（' + (STATUS_LABELS[status] || status) + '）';
    }).join('、');
  }
  function unsupportedObjective(q, o) {
    if (['talk','collect','gather','interact'].includes(o.type)) return '';
    if (o.type === 'kill' && o.arena && o.enemyRole) return '';
    if (o.type === 'instance' && INSTANCE_LABELS[o.instance]) return '';
    if (o.type === 'puzzle' && ['bq-repair','jz-catch'].includes(o.puzzle)) return '';
    if (o.type === 'catch' && o.venue === 'qingxi-town') return '';
    if (o.type === 'route' && (q.id === 'yfc_q05' && o.worldObject === 'yfc_chase' || q.id === 'yfc_q08' && o.worldObject === 'yfc_pilot')) return '';
    return '需手动处理：' + (o.text || o.type) + '（' + o.type + '）';
  }
  function questRows(includeLocked = false, kind = state.story?.kind || '主线') {
    const data = window.__quests?.snapshot?.();
    if (!questCatalog || !data?.states) return [];
    const npc = id => {
      const n = questCatalog.npcs.find(row => row.id === id);
      return n ? { id: n.id, name: n.name, modelKey: n.modelKey, position: n.positionXZ,
        region: n.region, location: n.location } : null;
    };
    return questCatalog.quests.map((q, index) => {
      const slot = data.states[q.id] || {};
      const objectives = (q.objectives || []).map(o => ({ ...o,
        progress: Number(slot.progress?.[o.id] || 0),
        required: Number(slot.required?.[o.id] ?? o.required ?? 1),
      }));
      const talk = objectives.find(o => o.type === 'talk' && o.progress < o.required);
      return { id: q.id, number: index + 1, title: q.title, kind: q.kind || '未分类', chapter: q.chapter || 1,
        unsupported: objectives.map(o => unsupportedObjective(q,o)).filter(Boolean),
        region: q.region, status: slot.status || 'locked', objectives,
        timers: slot.timers || {}, giver: npc(q.giver), receiver: npc(q.receiver),
        contact: npc(slot.status === 'available' ? q.giver : slot.status === 'ready' ? q.receiver : talk?.npc),
        choices: q.rewards?.choices || [], prerequisites: q.prerequisites || [],
        targets: objectives.filter(o => o.progress < o.required).map(o => ({
          text: o.text, arena: o.arena || null, instance: o.instance || null,
          mode: o.instance ? '剧情副本' : o.arena ? '郊外探索（不是每日试炼）' : o.type,
        })),
      };
    }).filter(q => matchesQuestKind(q.kind, kind) && (includeLocked || ['available', 'active', 'ready'].includes(q.status)));
  }
  async function mainline(kind = '主线') {
    return workflow(async owner => {
      await prepareTown(owner);
      await until(() => !!window.__quests, 30000, '任务控制器；请启用脚本后刷新', owner);
      await loadQuestCatalog(); checkOwner(owner);
      const quests = window.__quests;
      await quests.refresh(); checkOwner(owner);
      if (quests !== window.__quests) throw new Error('任务场景已切换，请重新查询');
      if (!quests.snapshot?.()?.states) throw new Error('任务进度未能载入');
      const rows = questRows(false, kind);
      state.questKind = kind; state.panelView='quests'; lastQuestSignature=''; renderQuestList();
      if (assistantPanel) assistantPanel.open = true;
      step('已读取' + kind + '任务 · ' + rows.length + ' 项可接取或进行中');
      return rows;
    });
  }
  function markQuest(id) {
    if (state.running || state.busy) return '请先停止自驾或等待当前操作完成';
    const row = questRows(false, null).find(q => q.id === id);
    if (!row) return '请先刷新主线任务列表';
    const currentRegion = window.__quests?.snapshot?.()?.world?.current || 'yue';
    if (row.region !== currentRegion) return '请先通过传送法阵前往任务地区：' + row.region;
    markedQuest = id;
    window.__quests.showOnMap(id);
    return '已在游戏地图标记：' + row.title;
  }
  function interactQuest(id) {
    if (state.running || state.busy) return '请先停止自驾或等待当前操作完成';
    const row = questRows(false, null).find(q => q.id === id);
    if (!row) return '请先刷新主线任务列表';
    if (!row.contact) return markQuest(id);
    const rt = window.__rt;
    const target = rt?.questSceneStats?.()?.npcs?.find(n => n.key === row.contact.modelKey);
    // selectTarget/interact 会检查实际距离、障碍与角色状态，不隔空接交任务。
    if (!target || !rt.selectTarget(target.id)) return '请先走近' + row.contact.name + '，关闭游戏内面板后再交互';
    rt.interact();
    return '已与' + row.contact.name + '交互；在游戏对话框接取、交付或选择奖励';
  }

  // ============ 全自动主线：只走实际移动、游戏交互及真实战斗 ============
  const storyData = () => window.__quests?.snapshot?.();
  const storySlot = id => storyData()?.states?.[id];
  // 同步 revision 变化不代表目标推进，也不能作为接取/交付成功的依据。
  const storyProgress = id => { const slot = storySlot(id); return JSON.stringify(slot ? { ...slot, revision: undefined } : slot); };
  const storyQuest = id => questCatalog?.quests.find(q => q.id === id);
  function storyCheck(owner) {
    checkOwner(owner);
    if (!state.story || window.__rt !== state.story.runtime || window.__quests !== state.story.quests || !shown('dwellingWorld')) {
      throw new Error('主线场景已关闭或切换，请重新启动');
    }
    if (Date.now() > state.story.deadline) throw new Error('主线运行达到设置的时限');
    if (state.batch?.status !== 'running' && readCombat()?.state === 'defeat' && !combatSettlementError(readCombat())) throw new Error('角色战败，请在游戏内恢复后继续主线');
  }
  async function storyWait(ms, owner) {
    const end = Date.now() + ms;
    do { storyCheck(owner); await sleep(Math.min(100, Math.max(0, end - Date.now()))); } while (Date.now() < end);
    storyCheck(owner);
  }
  async function storyReady(owner) {
    storyCheck(owner);
    const start = Date.now();
    while (document.hidden || readCombat()?.paused || window.__rt.mobileHudState?.()?.blocked || shown('worldLoading')) {
      releaseKeys(); window.__rt.setFlightVerticalInput?.(0); loadingFailure();
      if (Date.now() - start > 420000) throw new Error('游戏长期暂停或加载未完成');
      await storyWait(200, owner);
    }
  }
  async function storyRequest(action, data, owner) {
    await storyReady(owner);
    releaseKeys();
    const result = await window.__quests.fieldRequest(action, data);
    storyCheck(owner);
    if (!result?.ok) throw new Error('任务请求未确认：' + (result?.error || action));
    return result;
  }
  function nextStoryQuest(maxChapter = Infinity) {
    const rows = questRows().filter(q => q.chapter <= maxChapter && !state.story?.deferred?.some(r=>r.id===q.id));
    const combat = readCombat();
    if (combat?.active && !combat.overworld && ['fighting','victory','defeat'].includes(combat.state)) {
      const inProgress = rows.find(q => q.status === 'active' && q.objectives.some(o => o.instance === combat.arena && o.progress < o.required));
      if (inProgress) return inProgress;
    }
    const rank = state.story?.kind !== '主线' ? { ready: 0, active: 1, available: 2 } : { ready: 0, available: 1, active: 2 };
    rows.sort((a, b) => {
      // 已经接到的限时任务优先完成、交付，避免在接新任务时过期。
      const timed = q => Object.keys(q.timers).length ? 0 : 1;
      return timed(a) - timed(b) || a.chapter - b.chapter || rank[a.status] - rank[b.status] || a.number - b.number;
    });
    return rows[0] || null;
  }

  // A* 在游戏碰撞查询上规划；每段细采样地面高度，避免切过墙角和跨越悬崖。
  class MinHeap {
    constructor() { this.items = []; }
    push(value) { const a = this.items; a.push(value); let i = a.length - 1;
      while (i) { const p = (i - 1) >> 1; if (a[p].f <= value.f) break; a[i] = a[p]; i = p; } a[i] = value; }
    pop() { const a = this.items, result = a[0], last = a.pop(); if (a.length) { let i = 0;
      while (i * 2 + 1 < a.length) { let c = i * 2 + 1; if (c + 1 < a.length && a[c + 1].f < a[c].f) c++;
        if (a[c].f >= last.f) break; a[i] = a[c]; i = c; } a[i] = last; } return result; }
  }
  function walkSegment(from, to, environment = fieldEnvironment, obstacles = []) {
    let y = from[1] || 0;
    const length = dist(from, to), count = Math.max(1, Math.ceil(length / 0.2));
    for (let i = 1; i <= count; i++) {
      const x = from[0] + (to[0] - from[0]) * i / count, z = from[2] + (to[2] - from[2]) * i / count;
      if (obstacles.some(o => Math.hypot(x - o[0], z - o[2]) < 0.8)) return null;
      if (environment?.canWalk && environment?.groundHeight) {
        const nextY = environment.groundHeight(x, z, y);
        if (!Number.isFinite(nextY) || Math.abs(nextY - y) > 0.43 || !environment.canWalk(x, z, 0.4, y)) return null;
        y = nextY;
      } else {
        // 没有碰撞运行时就不猜道路（重新进入坊市即可重新捕获）。
        return null;
      }
    }
    return [to[0], y, to[2]];
  }
  function findPath(from, goal, radius = 2, environment = fieldEnvironment, obstacles = [], options = {}) {
    const takeoff = options.takeoff === true;
    if (!takeoff && walkGoalReached(from, goal, radius, environment)) return [];
    const endpoint = [goal[0] + (from[0] - goal[0]) * radius * .75 / Math.max(.001, dist(from, goal)), 0,
      goal[2] + (from[2] - goal[2]) * radius * .75 / Math.max(.001, dist(from, goal))];
    const direct = !takeoff && walkSegment(from, endpoint, environment, obstacles);
    if (direct && walkGoalReached(direct, goal, radius, environment)) return [direct];
    const cell = takeoff ? .6 : 1.2, heap = new MinHeap(), best = new Map(), closed = new Set();
    const start = { x: 0, z: 0, point: from.slice(), g: 0, f: takeoff ? 0 : dist(from, goal), prev: null };
    heap.push(start); best.set('0,0', 0);
    const limit = takeoff ? 36 : dist(from, goal) + 160;
    // 起飞点只在脚边搜索，以更细网格先穿过门口，避免整段长路先被步行寻路拒绝。
    for (let count = 0; heap.items.length && count < (takeoff ? 5000 : 45000); count++) {
      const node = heap.pop(), key = node.x + ',' + node.z;
      if (closed.has(key)) continue; closed.add(key);
      if (takeoff ? dist(from, node.point) >= 2 && takeoffGround(node.point) : walkGoalReached(node.point, goal, radius, environment)) {
        const path = []; for (let n = node; n.prev; n = n.prev) path.push(n.point); return path.reverse();
      }
      for (const [dx, dz] of [[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]]) {
        const x = node.x + dx, z = node.z + dz, nextKey = x + ',' + z;
        if (closed.has(nextKey)) continue;
        const point = [from[0] + x * cell, 0, from[2] + z * cell], h = takeoff ? 0 : dist(point, goal);
        if (dist(from, point) + h > limit) continue;
        const g = node.g + Math.hypot(dx, dz) * cell;
        if (g >= (best.get(nextKey) ?? Infinity)) continue;
        const walkable = walkSegment(node.point, point, environment, obstacles);
        if (!walkable) continue;
        best.set(nextKey, g); heap.push({ x, z, point: walkable, g, f: g + h, prev: node });
      }
    }
    return null;
  }
  // 先把方向转换为游戏实际执行的八组按键，再检查该方向，而不是检查理想直线。
  function chooseWalkInput(world, target, environment = fieldEnvironment, obstacles = [], boundary = null, hazard = null) {
    const p = world?.actorPosition;
    if (!p || !target) return null;
    const basis = movementBasis(world), horizon = Math.min(1, Math.max(.7, dist(p, target)));
    let best = null;
    for (const keys of KEY_SETS) {
      const heading = keyHeading(keys, basis);
      const next = [p[0] + Math.sin(heading) * horizon, p[1], p[2] + Math.cos(heading) * horizon];
      const bounds = (Array.isArray(boundary) ? boundary : [boundary]).filter(Boolean);
      if (bounds.some(b => dist(next,b.center) > b.radius && dist(next,b.center) >= dist(p,b.center) - .01)) continue;
      const point = walkSegment(p, next, environment, obstacles);
      if (!point) continue;
      // 在场边/石块旁选择实际可走的出圈方向，不能因理想方向受阻反而走进落点。
      const danger=(Array.isArray(hazard)?hazard:[hazard]).reduce((sum,h)=>sum+(h?.locked?8*Math.max(0,h.radius-dist(point,h.center)):0),0);
      const score = dist(point, target)+danger;
      if (!best || score < best.score) best = { keys, heading, basis, point, score };
    }
    return best;
  }
  function outdoorGround(point) {
    const env = fieldEnvironment;
    if (!point || !env?.canFly || !env?.canWalk || !env?.groundHeight) return null;
    // 游戏的大世界环境可没有屋顶接口；是否能起飞仍由实际 canFly 检查。
    if (Number.isFinite(env.roofLandingHeightAt?.(point[0], point[2]))) return null;
    const y = env.groundHeight(point[0], point[2], 0);
    if (!Number.isFinite(y) || !env.canWalk(point[0], point[2], .42, y) || !env.canFly(point[0], point[2], .4, y)) return null;
    return [point[0], y, point[2]];
  }
  // 适配器可能没有屋顶接口，但不同参考高度仍会返回真实的上下层支撑面。
  function raisedSurface(point, env = fieldEnvironment) {
    if (!point || !env?.groundHeight) return false;
    const support = env.groundHeight(point[0], point[2], point[1]), base = env.groundHeight(point[0], point[2], 0);
    return Number.isFinite(support) && Number.isFinite(base) && support > base + .43 && Math.abs(support - point[1]) <= .43;
  }
  function walkGoalReached(point, goal, radius, env = fieldEnvironment) {
    if (dist(point, goal) > radius) return false;
    if (!raisedSurface(point, env)) return true;
    const targetY = env.groundHeight(goal[0], goal[2], goal[1]);
    return Number.isFinite(targetY) && Math.abs(point[1] - targetY) <= .43;
  }
  function landingGround(point) {
    const ground = outdoorGround(point);
    if (!ground) return null;
    const upper = fieldEnvironment.groundHeight(point[0], point[2], Math.max(point[1], ground[1] + CFG.flightAltitude + 24));
    // 游戏急降从空中高度选支撑面，不能把屋顶下面的地板当作室外落点。
    return Number.isFinite(upper) && Math.abs(upper - ground[1]) <= .43 ? ground : null;
  }
  function takeoffGround(point) {
    const ground = landingGround(point);
    if (ground) return ground;
    if (!raisedSurface(point)) return null;
    const y = fieldEnvironment.groundHeight(point[0], point[2], point[1]);
    const upper = fieldEnvironment.groundHeight(point[0], point[2], y + CFG.flightAltitude + 24);
    return Number.isFinite(upper) && Math.abs(upper - y) <= .43 &&
      fieldEnvironment.canWalk(point[0], point[2], .42, y) && fieldEnvironment.canFly(point[0], point[2], .4, y)
      ? [point[0], y, point[2]] : null;
  }
  function flightCombatBlocked() {
    const combat = readCombat();
    return !!combat?.active && (!combat.overworld || livingEnemies(combat).some(e => e.engaged));
  }
  function travelFlightDecision(world, goal, radius, options = {}) {
    const access = flightRuntime?.access, info = flightRuntime?.info;
    const distance = dist(world.actorPosition,goal), remaining = Math.max(0,distance-radius);
    let reason = '', needsTakeoffWalk = false;
    if (options.flight === false || state.story?.options.flight === false) reason='本段不使用御空';
    // recovery 只在步行无路或反复卡路后启用；断桥两端很近也仍需御空。
    else if (!options.recovery && distance < CFG.flightMinDistance)
      reason='目标不足 '+CFG.flightMinDistance+' 米，步行接近';
    else if (flightRuntime?.runtime !== window.__rt || !fieldEnvironment) reason='御空尚未就绪，等待场景准备';
    else if (typeof window.__rt.toggleFlight !== 'function' || typeof window.__rt.dropFlight !== 'function' ||
      typeof window.__rt.setFlightVerticalInput !== 'function') reason='当前场景不支持御空';
    else if (world.flight?.active) reason='已经在御空';
    else if (info?.starting) reason='等待起飞确认';
    else if (world.flight?.chariotMounted) reason='御风车行进中';
    else if (flightCombatBlocked()) reason='先脱离当前战斗';
    else if (access?.ready && !access.eligible) reason='当前修为未达到御空要求';
    else if (access?.ready && access.locked) reason='角色正在其他窗口御空';
    else if (access?.ready && Number(access.current)<8) reason='遁光法力不足';
    else if (info?.enabled===false) reason='游戏暂未允许起飞';
    else if (Date.now()<(state.story?.flightRetryAt || 0)) reason='起飞未获确认，稍后重试';
    // 建筑镜头可能在门口仍保持室内取景；起飞资格遵循游戏实际 canFly 碰撞查询。
    else if (!takeoffGround(world.actorPosition)) {
      reason=world.cameraFraming?.buildingId ? '先步行离开建筑' : '先步行到可起飞的室外空地'; needsTakeoffWalk=true;
    }
    return {allowed:!reason,reason:reason || '可御空',distance,remaining,needsTakeoffWalk};
  }
  function canTravelFly(world, goal, radius, options) {
    return travelFlightDecision(world,goal,radius,options).allowed;
  }
  function flightSegment(from, to, radius = .4) {
    const length = Math.hypot(...to.map((v, i) => v - from[i])), count = Math.max(1, Math.ceil(length / .35));
    for (let i = 1; i <= count; i++) {
      const p = from.map((v, k) => v + (to[k] - v) * i / count);
      if (!fieldEnvironment.canFly(p[0], p[2], radius, p[1])) return false;
      const roof = fieldEnvironment.roofLandingHeightAt?.(p[0], p[2]);
      if (Number.isFinite(roof) && p[1] < roof - .02 && p[1] + 1.78 > roof) return false;
    }
    return true;
  }
  function flightLandingPoint(goal, radius, from) {
    const candidates = [];
    // 留出落地后的步行路段；室内目标在门外寻找能走到目标的空地。
    // 小石台/窄崖壁只在目标附近有地面：先试目标本身和近距离环，再试外围大环。
    const rings=[0, Math.max(.5, radius * .5), Math.max(1.5, radius), Math.max(2, radius * 1.5),
      Math.max(4, radius * 2), Math.max(8, radius + 2), 16, 24, 36];
    for (const distance of [...new Set(rings.map(v => Math.round(v * 10) / 10))]) {
      for (let i = 0; i < 16; i++) {
        const angle = bearingTo(goal, from) + i * Math.PI / 8;
        const point = landingGround([goal[0] + Math.sin(angle) * distance, goal[1], goal[2] + Math.cos(angle) * distance]);
        if (point && flightSegment([point[0], point[1] + CFG.flightAltitude, point[2]], point)) candidates.push(point);
      }
    }
    candidates.sort((a, b) => dist(from, a) + dist(a, goal) * 2 - dist(from, b) - dist(b, goal) * 2);
    const outside = candidates.slice(0, 8).find(p => findPath(p, goal, radius));
    if (outside) return outside;
    // 小浮岛可能仅目标范围内有地面；外围无路时再检查中心和有效半径内的落点。
    const inside = [goal];
    for (let i = 0; i < 16; i++) {
      const angle = bearingTo(goal, from) + i * Math.PI / 8, distance = Math.max(.5, radius * .65);
      inside.push([goal[0] + Math.sin(angle) * distance, goal[1], goal[2] + Math.cos(angle) * distance]);
    }
    return inside.map(p => landingGround(p)).find(p => p &&
      flightSegment([p[0], p[1] + CFG.flightAltitude, p[2]], p) && findPath(p, goal, radius)) || null;
  }
  function cancelStoryFlight(story) {
    if (!story) return;
    if (story.flightRequest) story.flightRequest.cancelled = true;
    story.runtime.setFlightVerticalInput?.(0);
    if (story.flying) story.runtime.dropFlight?.();
    if (flightRuntime?.runtime === story.runtime && !flightRuntime.info?.starting && !readWorld()?.flight?.active) flightRuntime.request = null;
  }
  // 已取得御风车（支线「御风初行」交付后 licensed）且在野外时，起飞改乘御风车（巡航 2.5 倍）。
  // 走原生「御车」按钮的同一入口：服务端登记、展开、起飞；试驾计时进行中不占用。
  function chariotAvailable() {
    const permit = storyData()?.yufeng, world = readWorld();
    return !!permit?.licensed && permit.race?.status !== 'running' && world?.environment?.tier === 4 &&
      typeof window.qingxiYufengBridge?.toggle === 'function' && typeof window.__rt?.setYufengMounted === 'function';
  }
  // 落地后原生会自动收车；没收就按一次原生「收车」，不让人物带着车步行或回洞府。
  async function stowChariot(owner) {
    if (!readWorld()?.flight?.chariotMounted) return;
    const settle = Date.now() + 4000;
    while (readWorld()?.flight?.chariotMounted && !readWorld()?.flight?.active && Date.now() < settle) await storyWait(200, owner);
    const flight = readWorld()?.flight;
    if (!flight?.chariotMounted || flight.active) return;
    try { await window.qingxiYufengBridge?.toggle?.(); } catch (error) { log('御风车收纳失败：' + error.message); }
    storyCheck(owner);
    const deadline = Date.now() + 10000;
    while (readWorld()?.flight?.chariotMounted && !readWorld()?.flight?.active && Date.now() < deadline) await storyWait(200, owner);
    if (readWorld()?.flight?.chariotMounted && !readWorld()?.flight?.active) log('御风车尚未收纳，请在游戏内点「收车」');
  }
  async function landStoryFlight(owner) {
    releaseKeys(); window.__rt.setFlightVerticalInput?.(0);
    const deadline = Date.now() + 30000;
    let requested = false;
    while (readWorld()?.flight?.active) {
      await storyReady(owner);
      if (Date.now() > deadline) throw new Error('御空落地超时，请在游戏内确认落点');
      const phase = readWorld()?.flight?.phase;
      if (!requested && !['landing', 'dive'].includes(phase)) {
        if (!window.__rt.dropFlight?.()) throw new Error('附近没有可用落点，请飞到空地后继续');
        requested = true;
      }
      await storyWait(CFG.tickMs, owner);
    }
    state.story.flying = false;
    await stowChariot(owner);
  }
  async function startStoryFlight(owner) {
    releaseKeys(); window.__rt.setFlightVerticalInput?.(0);
    const story = state.story, tracked = flightRuntime;
    const request = { cancelled: false, pitch: readWorld()?.pitch || 0 };
    story.flightRequest = request; story.flying = true;
    if (tracked?.runtime === story.runtime) tracked.request = request;
    let boarded = false;
    if (!readWorld()?.flight?.active && chariotAvailable() && Date.now() >= (story.chariotRetryAt || 0)) {
      try { await window.qingxiYufengBridge.toggle(); } catch (error) { log('御风车展开失败：' + error.message); }
      storyCheck(owner);
      // 原生起飞被拒时会自行收车；展开状态保留即表示起飞请求已被接受。
      boarded = !!(readWorld()?.flight?.chariotMounted || readWorld()?.flight?.active);
      if (boarded) log('展开御风车起飞');
      else { story.chariotRetryAt = Date.now() + 60000; log('御风车未能展开，本次改用御空'); }
    }
    story.flightVehicle = boarded || readWorld()?.flight?.chariotMounted ? '御风车' : '御空';
    if (!boarded && !readWorld()?.flight?.active && !story.runtime.toggleFlight?.()) {
      request.cancelled = true; story.flying = false;
      if (tracked?.request === request && !tracked.info?.starting) tracked.request = null;
      return false;
    }
    const deadline = Date.now() + 15000;
    while (true) {
      await storyReady(owner);
      const flight = readWorld()?.flight;
      if (flight?.active && flight.phase === 'cruise') return true;
      if (Date.now() > deadline || tracked?.info?.starting === false && !flight?.active ||
        ['landing', 'dive'].includes(flight?.phase)) {
        request.cancelled = true;
        await landStoryFlight(owner); return false;
      }
      await storyWait(CFG.tickMs, owner);
    }
  }
  function chooseFlightInput(world, target, ms) {
    const p = world.actorPosition, speed = world.flight.cruiseSpeed || 25;
    const pitch = Math.max(-1.25, Math.min(1.25, (world.pitch || 0) - state.story.flightRequest.pitch));
    const basis = movementBasis(world);
    // 近落点时缩短预测步长，避免最小预测距离超过剩余距离而在屋檐边悬停。
    const targetDistance = Math.hypot(...target.map((v, i) => v - p[i]));
    const lookAhead = Math.min(Math.max(.18, ms / 1000 * 2), Math.max(.02, targetDistance / speed * .7));
    let best = null;
    for (const keys of [...KEY_SETS, []]) {
      let forward = Number(keys.includes('w')) - Number(keys.includes('s'));
      let right = Number(keys.includes('d')) - Number(keys.includes('a'));
      const length = Math.max(1, Math.hypot(forward, right)); forward /= length; right /= length;
      const vertical = Math.max(-1, Math.min(1, ((target[1] - p[1]) / (speed * lookAhead) - Math.sin(pitch) * forward) / .4));
      let dx = -Math.sin(basis) * forward * Math.cos(pitch) + Math.cos(basis) * right;
      let dz = -Math.cos(basis) * forward * Math.cos(pitch) - Math.sin(basis) * right;
      let dy = Math.sin(pitch) * forward + vertical * .4;
      const scale = Math.max(1, Math.hypot(dx, dy, dz)); dx /= scale; dy /= scale; dz /= scale;
      const next = [p[0] + dx * speed * lookAhead, p[1] + dy * speed * lookAhead, p[2] + dz * speed * lookAhead];
      const score = Math.hypot(...target.map((v, i) => v - next[i]));
      if (score >= Math.hypot(...target.map((v, i) => v - p[i])) - .02 || !flightSegment(p, next, flightHull(world))) continue;
      if (!best || score < best.score) best = { keys, vertical, score };
    }
    return best;
  }
  async function travelFly(goal, radius, owner, options) {
    const landing = flightLandingPoint(goal, radius, myPos());
    // 算不出落点时退一步：飞到目标正上方再急降，由游戏自己挑脚下的支撑面。
    const dive = !landing, spot = dive ? [goal[0], goal[1], goal[2]] : landing;
    if (dive) {
      // 急降的前提是两端上空都能飞；起飞点或目标上空被封住时照旧先步行，条件恢复后再试。
      const here = myPos(), env = fieldEnvironment, high = CFG.flightAltitude;
      const open = typeof env?.canFly === 'function' && env.canFly(here[0], here[2], .4, here[1] + high) && env.canFly(goal[0], goal[2], .4, goal[1] + high);
      if (!open) { state.story.flightLastFailure='目标附近没有可达的室外落点'; log(state.story.flightLastFailure+'，继续步行后重试'); return; }
    }
    try {
      if (!await startStoryFlight(owner)) {
        state.story.flightRetryAt = Date.now() + 60000;
        state.story.flightLastFailure='御空未获确认'; log(state.story.flightLastFailure+'，继续步行后重试'); return;
      }
      state.story.flightLastFailure='';
      const vehicle = state.story.flightVehicle || '御空';
      log((options.recovery ? '步行受阻，'+vehicle+'脱困' : '室外长距离'+vehicle) + ' · 距离 ' + Math.round(dist(myPos(), goal)) +
        (dive ? ' · 目标正上方急降' : ''));
      const target = [spot[0], Math.max(myPos()[1], spot[1] + CFG.flightAltitude), spot[2]];
      const ceiling = target[1] + 24, deadline = Date.now() + Math.max(45000, dist(myPos(), spot) * 250);
      let closest = Infinity, progressedAt = Date.now();
      while (true) {
        await storyReady(owner);
        const world = readWorld(), p = world.actorPosition;
        if (!world.flight?.active || ['landing', 'dive'].includes(world.flight.phase)) { log('御空已结束，落地后继续步行'); break; }
        if (options.interrupt?.()) break;
        if (dive) {
          // 只要站到目标正上方；急降后的支撑面由游戏决定，落地后再核对是否进入交互范围。
          if (Math.hypot(p[0] - goal[0], p[2] - goal[2]) <= 2.2 && p[1] >= goal[1]) { log('已到目标正上方，急降'); break; }
        } else {
          // 岛边两米范围仍可能在裂隙或另一侧地面，落地后必须确有到目标的步行路线。
          const ground = dist(p, landing) <= 2 && landingGround(p);
          if (ground && findPath(ground, goal, radius)) break;
        }
        if (Date.now() > deadline || Date.now() - progressedAt > 5000) { log('御空路线受阻，落地后改走步行路线'); break; }
        const mana = flightRuntime.info?.manaCurrent;
        if ((Number.isFinite(mana) && mana <= 8 || flightCombatBlocked()) && landingGround(p)) {
          state.story.flightRetryAt = Date.now() + 60000;
          log('御空条件变化，落地后继续'); break;
        }
        const distance = Math.hypot(...target.map((v, i) => v - p[i]));
        if (distance < closest - .5) { closest = distance; progressedAt = Date.now(); }
        const ms = Math.max(20, Math.min(CFG.tickMs, dist(p, spot) / (world.flight.cruiseSpeed || 25) * 300));
        const input = chooseFlightInput(world, target, ms);
        if (input) { setKeys(input.keys); window.__rt.setFlightVerticalInput(input.vertical); }
        else {
          releaseKeys(); window.__rt.setFlightVerticalInput(0);
          if (target[1] < ceiling) { target[1] = Math.min(ceiling, target[1] + 6); closest = Infinity; }
        }
        state.navigation = { mode: 'flight', position: p.slice(), goal: goal.slice(), waypoint: target.slice(),
          distance: dist(p, goal), keys: input?.keys.join('') || '', vertical: input?.vertical || 0, updatedAt: Date.now() };
        await storyWait(ms, owner);
      }
      if (!await landOrLeave(owner)) {
        // 目标上空不能降落：已改落到别处，这一段不再反复起飞。
        state.story.flightLastFailure='目标上空没有可用落点';
        state.story.flightRetryAt = Date.now() + 60000;
        log(state.story.flightLastFailure+'，已飞到开阔地落地');
      } else if (dive && !walkGoalReached(myPos(), goal, Math.max(radius, 4))) {
        state.story.flightLastFailure='急降后仍未到达目标';
        // 同一处不反复起飞急降；一分钟内交给步行，仍到不了由上层报错或跳过该点。
        state.story.flightRetryAt = Date.now() + 60000;
        log(state.story.flightLastFailure+'，继续步行后重试');
      }
    } finally {
      if (owner === state.generation) cancelStoryFlight(state.story);
    }
  }
  // ============ 空中航线：越过山脊飞到目标上空 ============
  // 用于地面不能起降的地形。青黛回环竞速山谷按原生规则要求离地 3.6 米以上才可飞，起飞检查和急降的
  // 最后一段都落在这条线以下，所以谷内既不能起飞也找不到落点；而题记只核对水平距离，可在上空悬停记入。
  // 原生（dwelling-3d.js）：御风车空中碰撞半径 3.6 米、离地不低于 4.5 米，御剑 / 遁光半径 0.4 米；
  // 升降速度是水平速度的 0.4 倍，离地高度上限 500 米。
  function flightHull(world = readWorld()) { return world?.flight?.chariotMounted ? 3.6 : .4; }
  function flightMana() {
    const info = flightRuntime?.info, access = flightRuntime?.access;
    const current = Number.isFinite(info?.manaCurrent) ? info.manaCurrent : Number(access?.current);
    const maximum = Number.isFinite(info?.manaMaximum) ? info.manaMaximum : Number(access?.maximum);
    return {current: Number.isFinite(current) ? current : null, maximum: Number.isFinite(maximum) && maximum > 0 ? maximum : null};
  }
  // 某一点上空最低的可飞高度；没有地面的场景之外返回 Infinity。
  function flightFloorAt(x, z, radius) {
    const env = fieldEnvironment;
    const heights = [env.groundHeight(x, z, 0), env.groundHeight(x, z, 1e5), env.roofLandingHeightAt?.(x, z)].filter(Number.isFinite);
    if (!heights.length) return Infinity;
    let y = Math.max(...heights) + 5;
    for (let i = 0; i < 160; i++, y += 3) if (env.canFly(x, z, radius, y)) return y;
    return Infinity;
  }
  function flightBlockedAt(from, to, radius) {
    const length = Math.hypot(...to.map((v, i) => v - from[i])), count = Math.max(1, Math.ceil(length / .5));
    for (let i = 1; i <= count; i++) {
      const p = from.map((v, k) => v + (to[k] - v) * i / count);
      if (!fieldEnvironment.canFly(p[0], p[2], radius, p[1])) return p;
      const roof = fieldEnvironment.roofLandingHeightAt?.(p[0], p[2]);
      if (Number.isFinite(roof) && p[1] < roof - .02 && p[1] + 1.78 > roof) return p;
    }
    return null;
  }
  // 直线航线的高度剖面：每 8 米取一站（含两侧余量）的最低可飞高度，再按可爬升的坡度前后抹平。
  // 返回折线路点；起点低于剖面时先原地升高，终点高于目标时最后原地下降。
  function planFlightRoute(from, to, hull) {
    const env = fieldEnvironment;
    if (!env?.canFly || !env?.groundHeight) return null;
    const length = Math.hypot(to[0] - from[0], to[2] - from[2]), radius = hull + 1.5;
    if (length < 1) return flightBlockedAt(from, to, hull + .3) ? null : [from.slice(), to.slice()];
    const count = Math.max(1, Math.ceil(length / 8)), side = hull + 4, slope = .3, stations = [];
    const ux = (to[0] - from[0]) / length, uz = (to[2] - from[2]) / length;
    for (let i = 0; i <= count; i++) {
      const s = length * i / count, x = from[0] + ux * s, z = from[2] + uz * s;
      let floor = -Infinity;
      for (const offset of [0, side, -side]) floor = Math.max(floor, flightFloorAt(x - uz * offset, z + ux * offset, radius));
      if (!Number.isFinite(floor)) return null;   // 航线穿出了有地面的范围
      stations.push({s, x, z, floor: floor + 4});
    }
    stations[0].floor = Math.max(stations[0].floor, from[1]);
    stations[count].floor = Math.max(stations[count].floor, to[1]);
    for (let attempt = 0; attempt < 8; attempt++) {
      const y = stations.map(p => p.floor);
      for (let i = 1; i <= count; i++) y[i] = Math.max(y[i], y[i - 1] - slope * (stations[i].s - stations[i - 1].s));
      for (let i = count - 1; i >= 0; i--) y[i] = Math.max(y[i], y[i + 1] - slope * (stations[i + 1].s - stations[i].s));
      // 两座高点之间不下探：填平到较低一侧的高度，少做无谓的升降，也离地面更远。
      const ahead = y.slice();
      for (let i = count - 1; i >= 0; i--) ahead[i] = Math.max(ahead[i], ahead[i + 1]);
      for (let i = 0, behind = -Infinity; i <= count; i++) { behind = Math.max(behind, y[i]); y[i] = Math.min(behind, ahead[i]); }
      const route = [from.slice()];
      const add = p => { const last = route[route.length - 1]; if (Math.hypot(p[0] - last[0], p[1] - last[1], p[2] - last[2]) > .5) route.push(p); };
      add([from[0], y[0], from[2]]);
      for (let i = 1; i <= count; i++) {
        // 只在坡度变化处设路点。
        const before = (y[i] - y[i - 1]) / (stations[i].s - stations[i - 1].s);
        const after = i < count ? (y[i + 1] - y[i]) / (stations[i + 1].s - stations[i].s) : NaN;
        if (i === count || Math.abs(after - before) > .02) add([stations[i].x, y[i], stations[i].z]);
      }
      add(to.slice());
      let blocked = null;
      for (let i = 1; i < route.length && !blocked; i++) {
        const a = route[i - 1], b = route[i], vertical = Math.hypot(b[0] - a[0], b[2] - a[2]) < .5;
        blocked = flightBlockedAt(a, b, vertical ? hull + .3 : radius);
      }
      if (!blocked) return route;
      // 拱门之类上下都能过的障碍：把受阻处抬到障碍上方后重算。
      const index = Math.max(0, Math.min(count, Math.round(Math.hypot(blocked[0] - from[0], blocked[2] - from[2]) / (length / count))));
      let free = blocked[1] + 3;
      for (let k = 0; k < 160 && !env.canFly(blocked[0], blocked[2], radius, free); k++) free += 3;
      for (const j of [index - 1, index, index + 1]) if (stations[j]) stations[j].floor = Math.max(stations[j].floor, free + 4);
    }
    return null;
  }
  async function flyFlightRoute(route, owner, {goal = route[route.length - 1], arrive = 2.5, guardMana = true} = {}) {
    const span = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
    const rest = route.map(() => 0);
    for (let i = route.length - 2; i >= 0; i--) rest[i] = rest[i + 1] + span(route[i], route[i + 1]);
    const end = route[route.length - 1], deadline = Date.now() + Math.max(60000, rest[0] * 400);
    let segment = 1, best = Infinity, progressedAt = Date.now();
    try {
      while (true) {
        await storyReady(owner);
        const world = readWorld(), p = world?.actorPosition, flight = world?.flight;
        if (!Array.isArray(p) || p.length < 3 || !p.every(Number.isFinite)) throw new Error('御空途中无法读取人物坐标');
        if (!flight?.active || ['landing', 'dive'].includes(flight.phase)) throw new Error('御空航线中断：飞行已结束');
        if (flight.phase !== 'cruise') { await storyWait(60, owner); continue; }
        // 沿航线推进：越过当前航段终点或已贴近就换下一段。
        let a, b, t;
        while (true) {
          a = route[segment - 1]; b = route[segment];
          const ab = b.map((v, i) => v - a[i]), squared = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2;
          t = squared > 1e-6 ? ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1] + (p[2] - a[2]) * ab[2]) / squared : 1;
          if (segment >= route.length - 1 || t < 1 && span(p, b) > 3) break;
          segment++;
        }
        t = Math.max(0, Math.min(1, t));
        const q = a.map((v, i) => v + (b[i] - v) * t), final = segment >= route.length - 1;
        const planar = Math.hypot(p[0] - end[0], p[2] - end[2]), level = Math.abs(p[1] - end[1]) <= 2;
        const remaining = span(q, b) + rest[segment] + span(p, q);
        if (remaining < best - .5) { best = remaining; progressedAt = Date.now(); }
        // 贴近终点后八向按键可能在一两米内来回，停止收敛时放宽到 1.6 倍。
        if (final && level && (planar <= arrive || planar <= arrive * 1.6 && Date.now() - progressedAt > 1500)) return;
        if (Date.now() > deadline) throw new Error('御空航线超时');
        if (Date.now() - progressedAt > 6000) throw new Error('御空航线受阻');
        const mana = flightMana();
        if (guardMana && mana.current != null && mana.current <= (mana.maximum ? mana.maximum * CFG.airManaAbort : 20))
          throw new Error('御空法力不足（' + Math.floor(mana.current) + '），中止航线');
        // 追踪点取航线前方一小段，高度跟随剖面；先升后平、先平后降这类大转折不提前越过，避免斜切进山体。
        const speed = flight.cruiseSpeed || 25;
        let target = b, look = Math.max(10, Math.min(40, speed * .45)), room = span(q, b);
        if (look < room) target = q.map((v, i) => v + (b[i] - v) * look / room);
        else {
          look -= room;
          for (let i = segment; look > 0 && i < route.length - 1; i++) {
            const u = route[i - 1], v = route[i], w = route[i + 1], first = span(u, v), second = span(v, w);
            const cosine = first > .01 && second > .01 ? ((v[0] - u[0]) * (w[0] - v[0]) + (v[1] - u[1]) * (w[1] - v[1]) + (v[2] - u[2]) * (w[2] - v[2])) / (first * second) : 0;
            if (cosine < .94) break;
            const advance = Math.min(look, second);
            target = v.map((value, k) => value + (w[k] - value) * advance / second); look -= advance;
          }
        }
        const ms = Math.max(20, Math.min(CFG.tickMs, remaining / speed * 300));
        const input = chooseFlightInput(world, target, ms);
        if (input) { setKeys(input.keys); window.__rt.setFlightVerticalInput(input.vertical); }
        else { releaseKeys(); window.__rt.setFlightVerticalInput(0); }
        state.navigation = { mode: 'flight', position: p.slice(), goal: goal.slice(), waypoint: target.slice(), distance: dist(p, goal),
          keys: input?.keys.join('') || '', vertical: input?.vertical || 0, route: {segment, points: route.length, remaining: Math.round(remaining)}, updatedAt: Date.now() };
        await storyWait(ms, owner);
      }
    } finally { if (owner === state.generation) { releaseKeys(); window.__rt?.setFlightVerticalInput?.(0); } }
  }
  // 飞到目标正上方并悬停（不落地）。已在空中时直接接着飞。
  async function hoverOverPoint(ground, owner, {label = '目标', arrive = 2.5} = {}) {
    const env = fieldEnvironment;
    let world = readWorld();
    if (!world?.flight?.active) {
      await stowChariot(owner); world = readWorld();
      const mana = flightMana();
      if (mana.current != null && mana.current < (mana.maximum ? mana.maximum * CFG.airManaStart : 60))
        throw new Error('御空法力不足（' + Math.floor(mana.current) + '），暂不前往' + label);
      const decision = travelFlightDecision(world, ground, 0, {recovery: true});
      if (!decision.allowed) throw new Error('无法御空前往' + label + '：' + decision.reason);
      const home = world.actorPosition.slice();
      if (!await startStoryFlight(owner)) {
        state.story.flightRetryAt = Date.now() + 60000;
        throw new Error('御空未获确认，暂时无法前往' + label);
      }
      state.story.airHome = home;
    } else if (!state.story.flightRequest || state.story.flightRequest.cancelled) {
      const request = {cancelled: false, pitch: world.pitch || 0};
      state.story.flightRequest = request;
      if (flightRuntime?.runtime === state.story.runtime) flightRuntime.request = request;
    }
    state.story.flying = true;
    const hull = flightHull();
    let y = ground[1] + CFG.airHoverHeight;
    for (let i = 0; i < 12 && !env.canFly(ground[0], ground[2], hull + 1, y); i++) y += 4;
    if (!env.canFly(ground[0], ground[2], hull + 1, y)) throw new Error(label + '上空没有可悬停的空域，御空无法靠近');
    const target = [ground[0], y, ground[2]];
    for (let attempt = 1; ; attempt++) {
      const route = planFlightRoute(readWorld().actorPosition.slice(), target, hull);
      if (!route) throw new Error('御空航线规划失败：无法越过途中的山体或障碍（' + label + '）');
      log('御空航线：水平 ' + Math.round(Math.hypot(target[0] - route[0][0], target[2] - route[0][2])) + ' 米，最高 ' +
        Math.round(Math.max(...route.map(point => point[1]))) + ' 米，' + (route.length - 1) + ' 段');
      try { await flyFlightRoute(route, owner, {goal: ground, arrive}); break; }
      catch (error) {
        if (error instanceof Cancelled || owner !== state.generation || attempt >= 3 || !/受阻|超时/.test(error.message)) throw error;
        log('御空航线受阻，原地重新规划（第 ' + attempt + ' 次）');
      }
    }
    const p = readWorld().actorPosition;
    state.navigation = { mode: 'hover', position: p.slice(), goal: ground.slice(), waypoint: target.slice(), distance: dist(p, ground), keys: '', updatedAt: Date.now() };
  }
  // 附近可以降落的开阔地（由近到远）。prefer 周围的落点排在最前，home（本次起飞点）作为保底。
  function openLandingSpots(from, {prefer = null, home = null} = {}) {
    const spot = (x, z) => {
      const ground = landingGround([x, 0, z]);
      if (!ground) return null;
      for (const [dx, dz] of [[2, 0], [-2, 0], [0, 2], [0, -2]]) if (!landingGround([x + dx, 0, z + dz])) return null;
      return flightSegment([ground[0], ground[1] + CFG.flightAltitude, ground[2]], ground) ? ground : null;
    };
    const ring = (center, radius, limit) => {
      const spots = [];
      for (let r = 0; r <= radius && spots.length < limit; r += r < 48 ? 8 : 24) {
        const count = r ? Math.max(8, Math.ceil(2 * Math.PI * r / 36)) : 1, row = [];
        for (let i = 0; i < count; i++) {
          const angle = i / count * 2 * Math.PI, ground = spot(center[0] + Math.sin(angle) * r, center[2] + Math.cos(angle) * r);
          if (ground) row.push(ground);
        }
        // 同一圈里取离出发处最近的一个。
        if (row.length) spots.push(row.sort((a, b) => dist(a, from) - dist(b, from))[0]);
      }
      return spots;
    };
    return [...(prefer ? ring(prefer, 40, 2) : []), ...ring(from, 2600, 3), ...(home ? ring(home, 16, 1) : [])];
  }
  // 正常降落；游戏在脚下找不到落点时（返回 false）改飞到最近的开阔地落地，不把人物留在半空。
  async function landOrLeave(owner) {
    try { await landStoryFlight(owner); return true; }
    catch (error) {
      if (error instanceof Cancelled || owner !== state.generation || !/没有可用落点/.test(error.message)) throw error;
      await leaveByAir(owner); return false;
    }
  }
  // 结束空中行程：脚下能降就直接降，否则飞到最近的开阔地再落地、收车。
  async function leaveByAir(owner, {prefer = null} = {}) {
    let world = readWorld();
    if (!world?.flight?.active) { await stowChariot(owner); return; }
    // 竞速山谷里没有落点：没有指定去处时飞回停靠石台（原生的御风车起降处）。
    if (!prefer && raceCorridorFor(world.actorPosition)) {
      const dock = position3(window.YufengRaceCourse.dock), y = fieldEnvironment?.groundHeight?.(dock[0], dock[2], 0);
      if (Number.isFinite(y)) prefer = [dock[0], y, dock[2]];
    }
    state.story.flying = true;
    if (!state.story.flightRequest || state.story.flightRequest.cancelled) {
      const request = {cancelled: false, pitch: world.pitch || 0};
      state.story.flightRequest = request;
      if (flightRuntime?.runtime === state.story.runtime) flightRuntime.request = request;
    }
    releaseKeys(); window.__rt.setFlightVerticalInput?.(0);
    const here = world.actorPosition.slice();
    // 指定了去处（例如停靠石台）且距离较远时不就地降落。
    const local = !prefer || dist(here, prefer) < 60;
    if (['landing', 'dive'].includes(world.flight.phase) || local && window.__rt.dropFlight?.()) { await landStoryFlight(owner); return; }
    const hull = flightHull(world);
    for (const spot of openLandingSpots(here, {prefer, home: state.story.airHome}).slice(0, 5)) {
      const route = planFlightRoute(readWorld().actorPosition.slice(), [spot[0], spot[1] + CFG.flightAltitude, spot[2]], hull);
      if (!route) continue;
      step('飞离无法降落的区域 · 最近落点 ' + Math.round(dist(here, spot)) + ' 米');
      try {
        await flyFlightRoute(route, owner, {goal: spot, arrive: 3, guardMana: false});
        await landStoryFlight(owner); return;
      } catch (error) {
        if (error instanceof Cancelled || owner !== state.generation) throw error;
        log('前往落点未成：' + error.message);
        if (!readWorld()?.flight?.active) { await stowChariot(owner); return; }
      }
    }
    throw new Error('附近没有可用落点，请手动飞到空地后继续');
  }
  async function walkTo(goal, radius, owner, options = {}) {
    let path = null, last = readWorld()?.actorPosition, movedAt = Date.now(), repairs = 0, lastInput = null, repairOrigin = null;
    let flightAttempt = null, walkRecovery = false;
    const obstacles = [], deadline = Date.now() + Math.max(90000, dist(last, goal) * 1500);
    try {
      while (true) {
        await storyReady(owner);
        const world = readWorld(), p = world?.actorPosition;
        if (!position3(p)) throw new Error('无法读取人物坐标');
        if (world.flight?.active) { state.story.flying = true; await landOrLeave(owner); continue; }
        if (options.interrupt?.()) {
          if (options.moonEvasion) observeMoonDiagnostics(readCombat(),'rune_walk_exit',{reason:'原生目标消失或战斗已结束',goal:moonPoint(goal)});
          return;
        }
        if (walkGoalReached(p, goal, radius)) {
          if (options.moonEvasion) observeMoonDiagnostics(readCombat(),'rune_walk_exit',{reason:'到达寻路半径',goal:moonPoint(goal),radius});
          return;
        }
        if (Date.now() > deadline) throw new Error('寻路超时：' + JSON.stringify(goal));
        if (options.defend !== false && await defendNearby(owner)) { path = null; movedAt = Date.now(); continue; }
        const flightOptions = { ...options, recovery: walkRecovery };
        const flightDecision = travelFlightDecision(world,goal,radius,flightOptions);
        // 一次失败只限制当前地点，走到新空地或等待后可再次尝试；每段最多三次。
        const flightRetryReady = !flightAttempt || flightAttempt.count<3 && Date.now()>=flightAttempt.at+5000 &&
          (dist(p,flightAttempt.position)>=8 || Date.now()>=flightAttempt.at+30000);
        if (flightRetryReady && flightDecision.allowed) {
          flightAttempt={count:(flightAttempt?.count || 0)+1,at:Date.now(),position:p.slice()};
          await travelFly(goal, radius, owner, flightOptions);
          path = null; last = myPos().slice(); movedAt = Date.now(); continue;
        }
        const flightStatus={...flightDecision,attempts:flightAttempt?.count || 0,lastFailure:state.story.flightLastFailure || ''};
        if (flightDecision.allowed && !flightRetryReady) {
          flightStatus.reason=flightAttempt.count>=3 ? '本段御空尝试三次未成，继续步行' : '步行到新空地后重试御空';
        }
        if (dist(last, p) > .3) { last = p.slice(); movedAt = Date.now(); }
        if (repairOrigin && dist(repairOrigin, p) > 4) { repairs = 0; repairOrigin = null; }
        if (Date.now() - movedAt > 3500) {
          releaseKeys();
          if (++repairs > 3) throw new Error('连续卡路，请检查门、台阶或障碍：' + JSON.stringify(p));
          // 漏报的障碍在脚下前方；远处路点可能就是任务目的地，不能将它封死。
          const heading = lastInput?.heading ?? bearingTo(p, path?.[0] || goal);
          obstacles.push([p[0] + Math.sin(heading) * 1.1, p[1], p[2] + Math.cos(heading) * 1.1]);
          if (obstacles.length > 8) obstacles.shift();
          repairOrigin = p.slice();
          if (repairs >= 2) walkRecovery = true;
          log('卡路重规划 ' + repairs + '/3 · 位置 ' + JSON.stringify(p));
          path = null; movedAt = Date.now();
        }
        if (!path?.length) {
          path = findPath(p, goal, radius, fieldEnvironment, obstacles, { takeoff: flightDecision.needsTakeoffWalk });
          if (!path && flightDecision.needsTakeoffWalk) path = findPath(p, goal, radius, fieldEnvironment, obstacles);
          if (!path) {
            walkRecovery = true;
            const recovery = travelFlightDecision(world, goal, radius, { ...options, recovery: true });
            state.navigation = { mode:'walk', position:p.slice(), goal:goal.slice(), waypoint:null,
              distance:dist(p,goal), flight:recovery, keys:'', repairs, obstacles:obstacles.map(o=>o.slice()), updatedAt:Date.now() };
            releaseKeys();
            // 无步行路线时允许短途御空，仍遵守游戏资格、碰撞、战斗与尝试次数限制。
            if (flightRetryReady && recovery.allowed) { log('步行路线不可达，检查御空脱困'); continue; }
            if (recovery.needsTakeoffWalk) path = findPath(p, goal, radius, fieldEnvironment, obstacles, {takeoff:true});
            if (!path) throw new Error('找不到可步行路线：' + JSON.stringify(goal) + ' · ' +
              (state.story?.flightLastFailure || recovery.reason));
          }
          if (!path.length) return;
        }
        while (path.length > 1 && dist(p, path[0]) < .85) path.shift();
        // 看得见后续路点时直接走，减少在短网格节点间摆动。
        for (let i = Math.min(path.length - 1, 12); i > 0; i--) {
          if (walkSegment(p, path[i], fieldEnvironment, obstacles)) { path.splice(0, i); break; }
        }
        // 阵纹只避仍在飞行的真实剑招；不能用普通战斗的十米撤退 / transition 冲击赶离祭坛。
        const combat=options.moonEvasion?readCombat():null;
        if (options.moonEvasion) observeMoonDiagnostics(combat);
        const moonDangers=combat?.arena==='moonpalace'?moonThreats(combat,p,performance.now()):null;
        const threat=moonDangers?.[0];
        const evasion=moonDangers?{want:threat?.want,mobile:!!threat?.mobile,critical:!!threat?.critical,
          roll:!!threat?.critical&&!threat.reaction.responded,threat}:null;
        if (combat) recordDamage(combat,performance.now());
        const walkTarget=evasion && (evasion.mobile || evasion.critical)?
          [p[0]+Math.sin(evasion.want)*3,p[1],p[2]+Math.cos(evasion.want)*3]:path[0];
        const hazards=evasion?[evasion.threat?.hazard,...groundHazards()].filter(Boolean):null;
        const input = chooseWalkInput(world, walkTarget, fieldEnvironment, obstacles, null, hazards);
        if (options.moonEvasion && moonDiagnosticContext && Date.now()-moonDiagnosticContext.lastWalkAt>=500) {
          moonDiagnosticContext.lastWalkAt=Date.now();
          observeMoonDiagnostics(combat,'rune_walk',{walk:{goal:moonPoint(goal),waypoint:moonPoint(path[0]),
            walkTarget:moonPoint(walkTarget),distance:moonNumber(dist(p,goal)),radius,repairs,pathNodes:path.length,
            keys:input?.keys||[],heading:moonNumber(input?.heading),noInput:!input,
            evading:!!(evasion?.mobile||evasion?.critical),threat:evasion?.threat?{move:evasion.threat.move,critical:!!evasion.critical,responded:!!evasion.threat.reaction?.responded,
              hazard:evasion.threat.hazard?{center:moonPoint(evasion.threat.hazard.center),radius:moonNumber(evasion.threat.hazard.radius)}:null}:null,
            obstacles:obstacles.slice(-8).map(moonPoint),hazards:hazards?.length||0}});
        }
        if (input) { setKeys(input.keys); lastInput = input; } else releaseKeys();
        if (evasion) dodgeThreat(combat,evasion,performance.now());
        state.navigation = { mode: 'walk', position: p.slice(), goal: goal.slice(), waypoint: path[0].slice(),
          distance: dist(p, goal), flight:flightStatus, keys: input?.keys.join('') || '', heading: input?.heading ?? null, repairs,
          obstacles: obstacles.map(o => o.slice()), updatedAt: Date.now() };
        await storyWait(CFG.tickMs, owner);
      }
    } catch (error) {
      if (options.moonEvasion) observeMoonDiagnostics(readCombat(),'rune_walk_error',{message:safeLogText(error.message,180),goal:moonPoint(goal),repairs});
      throw error;
    } finally { if (owner === state.generation) releaseKeys(); }
  }

  // 地点来自任务目录与页面传送菜单；点击原有菜单，由页面执行地区解锁和资源加载。
  const STORY_DESTINATIONS = [
    { id: 'qingxi-travel-array', label: '返回清溪坊 · 传送法阵', point: [0,0,0], zone: 'qingxi' },
    { id: 'huangfeng-gate', label: '前往黄枫谷', point: [-1607,0,-2398], zone: 'huangfeng' },
    { id: 'tiantian-portal', label: '阗天城 · 传送台', point: [3200,0,3250], zone: 'tiantian' },
    { id: 'refugee-camp-gate', label: '九国盟 · 七派营', point: [3200,0,4280], zone: 'tiantian' },
    { id: 'tiantian-border-fort', label: '九国盟 · 慕兰边墙', point: [3200,0,5820], zone: 'jiuguo-border' },
    { id: 'mulan-wasteland-entry', label: '御风车支线 · 前往黄土荒野', point: [5310,0,3360], zone: 'mulan' },
    { id: 'yunmeng-portal', label: '天道盟 · 云梦坊传送台', point: [3200,0,-4300], zone: 'yunmeng' },
    { id: 'baiqiao-portal', label: '天道盟 · 百巧院', point: [5000,0,-4190], zone: 'baiqiao' },
    { id: 'jianzhong-portal', label: '天道盟 · 古剑门剑冢', point: [6500,0,-4230], zone: 'jianzhong' },
  ];
  function destinationNear(point) { return STORY_DESTINATIONS.reduce((a, b) => dist(a.point, point) <= dist(b.point, point) ? a : b); }
  function uiButton(selector, predicate) {
    return [...document.querySelectorAll(selector)].find(b => !b.disabled && !b.closest('[hidden]') && predicate(b.textContent.trim()));
  }
  async function approachTravelPortal(owner) {
    const rt = window.__rt, p = myPos();
    const portals = (readWorld()?.targets || []).filter(t => t.kind === 'region-portal').sort((a, b) => dist(p, a.point) - dist(p, b.point));
    if (!portals.length) throw new Error('当前区域未找到传送法阵');
    const portal = portals[0];
    if (!Number.isFinite(portal.radius) || portal.radius <= .5) throw new Error('传送法阵范围资料无效');
    const usable = () => rt.canUseTravelArray?.(portal.id) === true;
    await walkTo(portal.point, portal.radius - .5, owner, { interrupt: usable });
    storyCheck(owner);
    if (typeof rt.canUseTravelArray === 'function' && !usable()) throw new Error('传送法阵暂不可用，请检查高度或战斗状态');
    return { rt, portal };
  }
  async function travelMenu(label, owner) {
    const { rt, portal } = await approachTravelPortal(owner);
    if (!rt.selectTarget(portal.id)) throw new Error('未能选中传送法阵');
    rt.interact();
    await until(() => shown('worldSheet'), 5000, '传送选择', owner);
    const b = uiButton('#worldSheetBody button', text => text.startsWith(label));
    if (!b) { clickOnce('worldSheetClose'); throw new Error('传送目的地尚未解锁：' + label); }
    const before = myPos(); b.click();
    await until(() => { loadingFailure(); return !shown('worldSheet') && !shown('worldLoading') && dist(before, myPos()) > 5; }, 420000, '传送到' + label, owner);
    storyCheck(owner);
    await window.__quests.refresh(); storyCheck(owner);
  }
  async function approach(point, radius, owner) {
    const destination = destinationNear(point);
    const current = window.qingxiZones?.zoneAt?.(...[myPos()[0], myPos()[2]])?.id || destinationNear(myPos()).zone;
    // 同区域远距离优先使用存在的营地传送点，避免穿越未开放地形。
    if (current !== destination.zone || destination.zone === 'tiantian' && dist(myPos(), point) > 550) {
      step('任务传送：' + destination.label);
      await prepareBatchPortal(owner);
      await travelMenu(destination.label, owner);
    }
    await walkTo(point, radius, owner);
  }
  async function approachNpc(npcId, owner) {
    const npc = questCatalog.npcs.find(n => n.id === npcId);
    if (!npc?.positionXZ) throw new Error('缺少任务人物位置：' + npcId);
    const point = position3(npc.positionXZ);
    const live = () => window.__rt.questSceneStats?.()?.npcs?.find(n => n.key === npc.modelKey);
    await approach(live()?.point || point, 2.2, owner);
    const target = live();
    if (!target || !window.__rt.selectTarget(target.id)) throw new Error('无法靠近任务人物：' + npc.name);
    return npc;
  }

  const JOURNAL_REGIONS={yue:'越国',jiuguo:'九国盟',tiandao:'天道盟'};
  function journalRows() {
    const points=storyData()?.journal?.points;
    if(!Array.isArray(points))return [];
    const rank=id=>({yue:0,jiuguo:1,tiandao:2})[id] ?? 99;
    // 与原生游记一致：区域按剧情顺序，区域内保持服务端目录顺序。
    return points.map((p,index)=>({...p,index})).sort((a,b)=>rank(a.region)-rank(b.region)||a.index-b.index)
      .map((p,index)=>({...p,number:index+1}));
  }
  function journalDestination(row) {
    if(row.area==='青黛回环') {
      if (storySlot('yfc_q01')?.status !== 'rewarded') throw new Error('题记「'+row.name+'」需先完成御风车支线「南陇之邀」，解锁黄土荒野入口');
      return STORY_DESTINATIONS.find(d=>d.id==='mulan-wasteland-entry');
    }
    if(row.area==='南宫婉洞府' || row.id==='yue-moon')return {id:'moon-palace',label:'南宫婉洞府 · 月殿',zone:'moon-palace',point:[2400,160,-1617],native:true};
    return destinationNear(position3(row.position));
  }
  async function travelJournalDestination(destination,owner) {
    // 普通游历使用游戏的原有目的地加载与传送 API；须真正站上可用法阵，不触发副本 start。
    if(destination.id==='moon-palace' && !storyData()?.moonPalaceKey)throw new Error('月殿游历尚未解锁，请先完成「月殿禁符」');
    await prepareBatchPortal(owner);
    const {rt,portal}=await approachTravelPortal(owner);
    if(!rt.canUseTravelArray?.(portal.id))throw new Error('题记传送法阵暂不可用');
    const scene=window.qingxiRegionHost?.destinationScene?.(destination.id);
    if(typeof scene?.ready!=='function')throw new Error('题记区域场景尚未开放：'+destination.label);
    let loaded=false, failed=null;
    Promise.resolve().then(()=>scene.ready()).then(()=>{loaded=true;},error=>{failed=error;});
    await until(()=>{if(failed)throw failed;loadingFailure();return loaded;},240000,'题记区域加载：'+destination.label,owner);
    storyCheck(owner);
    if(!rt.canUseTravelArray?.(portal.id))throw new Error('离开法阵，已取消题记传送');
    if(!rt.visitTownOutland(destination.id))throw new Error('题记传送失败：'+destination.label);
    await until(()=>{loadingFailure();return !shown('worldLoading') && sceneReady() && dist(myPos(),destination.point)<80;},420000,'题记区域到达确认',owner);
    await window.__quests.refresh();storyCheck(owner);
  }
  async function approachJournal(row,owner) {
    const destination=journalDestination(row), point=position3(row.position);
    if(!point || !Number.isFinite(row.radius) || row.radius<=1)throw new Error('题记点位资料无效：'+row.name);
    // 加入特殊岛屿后再判定当前区域；基础 outlands 可能尚未载入九国盟和天道盟目的地。
    const candidates=[...STORY_DESTINATIONS,{zone:'moon-palace',point:[2400,160,-1617]},destination];
    const current=candidates.reduce((a,b)=>dist(myPos(),a.point)<=dist(myPos(),b.point)?a:b).zone;
    if(current!==destination.zone || destination.zone==='tiantian' && dist(myPos(),point)>550) {
      step('题记传送：'+destination.label);
      if(destination.native)await travelJournalDestination(destination,owner);
      else {await prepareBatchPortal(owner);await travelMenu(destination.label,owner);}
    }
    // 浮岛和高台以当前场景的实际支撑面为准，不能将二维目录点强制当成海拔 0。
    const y=fieldEnvironment?.groundHeight?.(point[0],point[2],0);
    if(!Number.isFinite(y))throw new Error('题记落点地形未就绪：'+row.name);
    point[1]=y;
    const radius=Math.min(3,row.radius-2);
    if(journalAirOnly(point,radius)) {
      // 地面无法起降：优先飞到上空悬停题记；空中不被接受或飞不过去时，改沿已知的地面通道步行。
      if(!state.story.journalGroundOnly) {
        try { await hoverOverJournal(row,point,owner); return 'air'; }
        catch(error) {
          if(error instanceof Cancelled || owner!==state.generation || !raceCorridorFor(point))throw error;
          log('题记空中接近未成（'+safeLogText(error.message,80)+'），改为步行接近');
        }
      }
      await approachJournalOnFoot(row,point,radius,owner);
      return 'ground';
    }
    await walkTo(point,radius,owner);
    return 'ground';
  }
  // 该处地面按原生规则不能起降（脚下不可飞而上空开阔），四周也没有落地后能走到的落点。
  function journalAirOnly(point,radius) {
    const env=fieldEnvironment;
    if(!env?.canFly || !env?.canWalk || !Number.isFinite(point?.[1]))return false;
    if(Number.isFinite(env.roofLandingHeightAt?.(point[0],point[2])))return false;
    if(env.canFly(point[0],point[2],.4,point[1]))return false;
    if(![6,12,24].every(height=>env.canFly(point[0],point[2],.4,point[1]+height)))return false;
    return !flightLandingPoint(point,radius,myPos());
  }
  async function hoverOverJournal(row,point,owner) {
    step('题记空中接近：'+row.name+'（地面无法起降，悬停题记）');
    await hoverOverPoint(point,owner,{label:'「'+row.name+'」',arrive:Math.min(2.5,row.radius-3)});
    if(state.journal?.current?.id===row.id)state.journal.current.mode='air';
    log('已悬停在「'+row.name+'」上空 '+Math.round(readWorld().actorPosition[1]-point[1])+' 米，水平偏差 '+dist(myPos(),point).toFixed(1)+' 米');
  }
  // 青黛回环竞速山谷：赛道中心线下方的谷底连续可走（原生只按坡度判断），从停靠石台沿各环的地面投影步行。
  function raceCorridorFor(point) {
    const course=window.YufengRaceCourse, bounds=course?.bounds;
    if(!Array.isArray(bounds) || bounds.length!==4 || !Array.isArray(course.dock) || !Array.isArray(course.gates) || course.gates.length<2)return null;
    if(point[0]<bounds[0] || point[0]>bounds[1] || point[2]<bounds[2] || point[2]>bounds[3])return null;
    const nodes=[position3(course.dock),...course.gates.map(gate=>position3(gate.position))];
    return nodes.every(Boolean)?nodes:null;
  }
  async function approachJournalOnFoot(row,point,radius,owner) {
    const nodes=raceCorridorFor(point);
    if(!nodes)throw new Error('题记「'+row.name+'」地面无法起降，也没有已知的步行路线');
    step('题记步行接近：'+row.name+'（沿青黛回环谷底）');
    const ground=node=>[node[0],fieldEnvironment.groundHeight(node[0],node[2],0),node[2]];
    const nearest=target=>nodes.reduce((best,node,index)=>dist(target,node)<dist(target,nodes[best])?index:best,0);
    await leaveByAir(owner,{prefer:ground(nodes[0])});
    let index=nearest(myPos());
    // 不在谷底通道上：先回到停靠石台（谷外可以正常御空和降落）。
    if(dist(myPos(),nodes[index])>60) {
      const dock=ground(nodes[0]), decision=travelFlightDecision(readWorld(),dock,6,{});
      if(!decision.allowed && dist(myPos(),dock)>300)throw new Error('题记「'+row.name+'」需先到停靠石台再步行，当前无法御空前往：'+decision.reason);
      await walkTo(dock,6,owner); index=0;
    }
    const last=nearest(point), direction=last>=index?1:-1;
    for(let i=index;i!==last+direction;i+=direction) {
      state.navigation={mode:'walk',position:myPos().slice(),goal:point.slice(),waypoint:ground(nodes[i]),distance:dist(myPos(),point),reason:'谷底通道 '+i+'/'+last,updatedAt:Date.now()};
      await walkTo(ground(nodes[i]),6,owner,{flight:false});
    }
    await walkTo(point,radius,owner,{flight:false});
    if(state.journal?.current?.id===row.id)state.journal.current.mode='ground';
  }
  async function collectJournalPoint(row,owner) {
    const found=()=>storyData()?.journal?.points?.find(p=>p.id===row.id)?.found===true;
    if(found())return;
    const mode=await approachJournal(row,owner);
    try { await recordJournalPoint(row,owner,found); }
    catch(error) {
      if(mode!=='air' || error instanceof Cancelled || owner!==state.generation || found())throw error;
      // 悬停时游戏没有给出题记或服务端未记入：这一轮不再尝试空中题记，改从地面走过去。
      log('空中题记未成（'+safeLogText(error.message,80)+'），改为步行接近');
      state.story.journalGroundOnly=true;
      await approachJournal(row,owner);
      await recordJournalPoint(row,owner,found);
    }
  }
  // 悬停时只要求水平位置稳定；飞剑、车体的轻微起伏不算移动，等不到也继续（题记只看水平距离）。
  async function waitForHover(owner) {
    const deadline=Date.now()+4000;
    let anchor=null, stableAt=Date.now();
    while(Date.now()<deadline) {
      await storyReady(owner);
      releaseKeys();window.__rt.setFlightVerticalInput?.(0);
      const p=readWorld()?.actorPosition;
      if(!position3(p))throw new Error('悬停时无法读取人物坐标');
      if(!anchor || dist(anchor,p)>.15) {anchor=p.slice();stableAt=Date.now();}
      if(Date.now()-stableAt>=300)return;
      await storyWait(80,owner);
    }
  }
  async function recordJournalPoint(row,owner,found) {
    const settle=()=>readWorld()?.flight?.active?waitForHover(owner):waitForStoryStill(owner);
    await settle();
    const readyBy=Date.now()+12000;
    while(!found()) {
      await storyReady(owner);
      if(await defendNearby(owner)) {await approachJournal(row,owner);await settle();continue;}
      const journal=window.tiannanJournal, target=journal?.stats?.();
      if(target?.target?.id===row.id && target.target.d<=row.radius-1 && !target.busy)break;
      if(Date.now()>readyBy)throw new Error('题记未能就近选中：'+row.name+
        (target?.target?'（游戏选中 '+target.target.id+'，相距 '+Number(target.target.d).toFixed(1)+' 米）':'（游戏没有给出附近的题记点）'));
      releaseKeys();await storyWait(200,owner);
    }
    if(found())return;
    storyCheck(owner);
    let completed=false, failure=null;
    // 原生 record 会处理按钮、防重入和文字展示；这里只观察真实进度，不自行伪造 found。
    Promise.resolve(window.tiannanJournal.record()).then(()=>{completed=true;},error=>{failure=error;completed=true;});
    const deadline=Date.now()+20000;
    while(!found()) {
      await storyReady(owner);
      if(failure)throw failure;
      if(Date.now()>deadline)throw new Error('题记收集未确认：'+row.name+'；服务端尚未记入，已暂停');
      if(await defendNearby(owner))continue;
      if(completed) {
        await window.__quests.refresh();storyCheck(owner);
        if(found())break;
        throw new Error('题记收集失败：'+row.name+'；服务端未记入，已暂停');
      }
      releaseKeys();await storyWait(200,owner);
    }
  }
  // 单个点位到不了（寻路/落点/御空）时本轮跳过并继续其它题记；传送、场景、前置等基础设施错误仍然中止。
  function skippableJournalError(error) {
    const text=String(error?.message || error);
    return /寻路|卡路|路线|落点|御空|未能到达|无法靠近/.test(text) && !/传送|法阵|场景|加载/.test(text);
  }
  async function collectJournal(options={}) {
    const maxPoints=options.maxPoints ?? Infinity,maxMinutes=options.maxMinutes ?? 180;
    if(options.maxPoints!=null && (!Number.isInteger(maxPoints)||maxPoints<1) || !Number.isFinite(maxMinutes)||maxMinutes<=0)return '题记参数无效：maxPoints 必须为正整数，maxMinutes 必须大于 0';
    return workflow(async owner=>{
      const combat=readCombat();
      if(combat?.active && !combat.overworld)throw new Error('请先结束当前副本，再收集题记');
      state.retry.enabled=false;state.panelView='journal';
      await prepareTown(owner);
      await until(()=>!!window.__quests && !!window.tiannanJournal,30000,'游记控制器',owner);
      if(!fieldEnvironment)throw new Error('未捕获题记道路数据，请保存脚本并刷新后再进入坊市');
      const rt=window.__rt,camera=rt.cameraFollowSettings?.()?.enabled;
      state.story={runtime:rt,quests:window.__quests,kind:'题记',options,deadline:Date.now()+maxMinutes*60000,current:null,completed:0,camera};
      state.journal={status:'running',current:null,completed:[],total:0,found:0};
      rt.setCameraFollowEnabled?.(false);
      try {
        window.__quests.close?.();window.tiannanJournal.close?.();clickOnce('worldSheetClose');canvas()?.focus?.({preventScroll:true});
        await window.__quests.refresh();storyCheck(owner);
        if(!journalRows().length)throw new Error('题记目录尚未加载');
        const skipped=new Map();
        while(true) {
          await storyReady(owner);
          const rows=journalRows(), row=rows.find(p=>!p.found && !skipped.has(p.id));
          state.journal.total=rows.length;state.journal.found=rows.filter(p=>p.found).length;
          if(!row || state.journal.completed.length>=maxPoints) {
            await settleJournalFlight(owner,null);
            await finishInCave(owner);checkOwner(owner);
            state.journal.status=row?'limited':'complete';state.journal.current=null;
            const result='题记本次收集 '+state.journal.completed.length+' 处，累计 '+state.journal.found+'/'+rows.length+
              (skipped.size?'，跳过 '+skipped.size+' 处（'+[...skipped.values()].join('；')+'）':'')+'；已返回洞府';
            step(result);return result;
          }
          state.journal.current={id:row.id,name:row.name,number:row.number};state.story.current=row.id;
          step('题记 '+row.number+'/'+rows.length+' · '+JOURNAL_REGIONS[row.region]+' · '+row.name);
          try { await collectJournalPoint(row,owner); }
          catch(error) {
            if (error instanceof Cancelled || owner!==state.generation || !skippableJournalError(error)) throw error;
            skipped.set(row.id,row.name+'：'+safeLogText(error.message,120));
            state.journal.skipped=[...skipped].map(([id,reason])=>({id,reason}));
            log('题记跳过 '+row.name+'：'+safeLogText(error.message,120));
            await settleJournalFlight(owner,skipped);
            continue;
          }
          storyCheck(owner);
          state.journal.completed.push(row.id);state.story.completed++;
          await settleJournalFlight(owner,skipped);
        }
      } catch(error) {
        if(owner===state.generation) {
          reportFailure(error);state.journal.status='error';state.journal.error=safeLogText(error.message);
          state.journal.found=journalRows().filter(p=>p.found).length;
          releaseKeys();cancelStoryFlight(state.story);
          // 保留题记原始异常，由公共 workflow 统一退出战斗并回洞府。
        }
        throw error;
      } finally {
        if(owner===state.generation) {
          releaseKeys();cancelStoryFlight(state.story);
          if(typeof camera==='boolean')window.__rt?.setCameraFollowEnabled?.(camera);
          state.story=null;
        }
      }
    });
  }
  // 悬停题记之后仍在空中：下一处若同样只能从空中记入就接着飞，否则先飞到开阔地落地、收车。
  // skipped 为 null 表示本轮结束。落不了地也不阻断流程，后续的回洞府 / 传送会结束飞行。
  async function settleJournalFlight(owner,skipped) {
    if(!readWorld()?.flight?.active)return;
    const next=skipped && !state.story.journalGroundOnly && state.journal.completed.length<(state.story.options?.maxPoints ?? Infinity)
      ? journalRows().find(row=>!row.found && !skipped.has(row.id)) : null;
    const point=next && position3(next.position);
    if(point && dist(myPos(),point)<3000) {
      const y=fieldEnvironment?.groundHeight?.(point[0],point[2],0);
      if(Number.isFinite(y)) { point[1]=y; if(journalAirOnly(point,Math.min(3,next.radius-2)))return; }
    }
    try { await leaveByAir(owner); }
    catch(error) {
      if(error instanceof Cancelled || owner!==state.generation)throw error;
      log('题记后未能落地：'+safeLogText(error.message,120));
    }
  }
  function returnJournalProtection() {
    const combat=readCombat();
    if(combat?.active && combat.state==='fighting' && !combat.paused)act('pause');
  }
  function inspectJournal() {
    state.panelView='journal';lastQuestSignature='';renderQuestList();
    const rows=journalRows();return rows.length?'题记已收集 '+rows.filter(p=>p.found).length+'/'+rows.length+'，按目录顺序显示':'请先进入青溪坊加载题记目录';
  }

  async function waitForStoryStill(owner) {
    const deadline = Date.now() + 5000;
    let anchor = null, stableAt = Date.now();
    while (true) {
      await storyReady(owner);
      releaseKeys(); window.__rt.setFlightVerticalInput?.(0);
      const p = readWorld()?.actorPosition;
      if (!position3(p)) throw new Error('停稳时无法读取人物坐标');
      if (!anchor || dist(anchor, p) > .025 || Math.abs(anchor[1] - p[1]) > .025) {
        anchor = p.slice(); stableAt = Date.now();
      }
      const stable = Date.now() - stableAt >= 400;
      if (state.navigation) state.navigation = { ...state.navigation, mode: stable ? 'idle' : 'settling',
        position: p.slice(), distance: dist(p, state.navigation.goal), keys: '', flight: null, updatedAt: Date.now() };
      if (stable) return p.slice();
      if (Date.now() > deadline) throw new Error('人物持续移动，无法停稳开始采集');
      await storyWait(80, owner);
    }
  }

  function questNodeApproach(node, fallbackRadius) {
    const target = (readWorld()?.targets || []).find(t => t.id === node.id);
    // 任务物品和机关可能位于不可步行区域内，按游戏公布的交互范围接近。
    const radius = Number.isFinite(target?.radius) && target.radius > .2 ? target.radius - .2 : fallbackRadius;
    return { point: target?.point || position3(node.position), radius };
  }
  async function gatherNode(node, owner) {
    const live = () => (readWorld()?.targets || []).find(t => t.id === node.id);
    const destination = questNodeApproach(node, Math.max(1, Math.min(2, (node.reach || 2.8) - .7)));
    await approach(destination.point, (node.rest || 0) > 3 ? 12 : destination.radius, owner);
    if ((node.rest || 0) > 3) {
      const target = live();
      if (!target) throw new Error('空中任务物品尚未加载');
      if (!await startStoryFlight(owner)) throw new Error('御空拾取未能起飞，请确认御空资格及法力');
      const deadline = Date.now() + 25000;
      while (true) {
        await storyReady(owner);
        const p = readWorld().actorPosition, dy = target.point[1] - p[1];
        if (Math.abs(dy) < .6 && dist(p, target.point) < 1.3) break;
        if (Date.now() > deadline || !readWorld().flight?.active) throw new Error('御空拾取未能抵达，可能法力不足');
        window.__rt.setFlightVerticalInput(Math.abs(dy) < .3 ? 0 : Math.sign(dy));
        if (dist(p, target.point) > 1) steer(bearingTo(p, target.point)); else releaseKeys();
        await storyWait(70, owner);
      }
      window.__rt.setFlightVerticalInput(0); releaseKeys();
    }
    const startPosition = await waitForStoryStill(owner);
    try {
      if (node.id.startsWith('loot:')) { await storyRequest('pickup', { nodeId: node.id }, owner); return; }
      const result = await storyRequest('gather-start', { nodeId: node.id }, owner);
      if (!result.gatherId || !Number.isFinite(result.duration)) throw new Error('采集开始未确认');
      const finishAt = Date.now() + result.duration * 1000 + 200;
      while (true) {
        storyCheck(owner);
        const p = readWorld()?.actorPosition;
        if (!position3(p) || dist(startPosition, p) > .16 || Math.abs(startPosition[1] - p[1]) > .2) {
          await storyRequest('gather-cancel', {}, owner); throw new Error('采集中发生位移，已中断');
        }
        if (Date.now() >= finishAt) break;
        await storyWait(Math.min(80, finishAt - Date.now()), owner);
      }
      await storyRequest('gather-finish', { nodeId: node.id, gatherId: result.gatherId }, owner);
    } finally {
      if (owner === state.generation && state.story?.flying) {
        await landStoryFlight(owner); cancelStoryFlight(state.story);
      }
    }
  }

  function rotateMask(mask, turns) { for (let i = 0; i < turns; i++) mask = ((mask << 1) | (mask >> 3)) & 15; return mask; }
  // 寻找一条源到核心的连通路，给沿途每块零件选择兼容旋转；锁定零件保持原样。
  function solveBoard(board) {
    if (!board || !Number.isInteger(board.w) || !Number.isInteger(board.h) || board.cells?.length !== board.w * board.h) return null;
    const rotations = board.cells.map((mask, index) => (board.locked || []).includes(index) ? [0] : [0,1,2,3]);
    const turns = board.cells.map(() => 0), visited = new Set(), end = board.core * board.w + board.w - 1;
    let budget = 200000;
    const directions = [[1,0,-1,4],[2,1,0,8],[4,0,1,1],[8,-1,0,2]];
    function search(index, incoming) {
      if (--budget < 0 || visited.has(index)) return false;
      visited.add(index);
      for (const r of rotations[index]) {
        const mask = rotateMask(board.cells[index], r); if (!(mask & incoming)) continue;
        turns[index] = r;
        if (index === end && (mask & 2)) return true;
        for (const [bit, dx, dy, opposite] of directions) {
          const x = index % board.w + dx, y = Math.floor(index / board.w) + dy;
          if (!(mask & bit) || x < 0 || y < 0 || x >= board.w || y >= board.h) continue;
          if (search(y * board.w + x, opposite)) return true;
        }
      }
      turns[index] = 0; visited.delete(index); return false;
    }
    return search(board.source * board.w, 8) ? turns : null;
  }
  function catchVisibleSword() {
    const hot = document.querySelector('.jz-catch:not([hidden]) button.hot');
    if (!hot) { if (state.story) state.story.catchAt = 0; return; }
    const now = performance.now();
    if (!state.story.catchAt) state.story.catchAt = now;
    if (now - state.story.catchAt < 350) return;
    hot.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));
    state.story.catchAt = 0;
  }
  async function puzzleStep(q, objective, owner) {
    const node = storyData()?.nodes?.find(n => n.itemId === objective.worldObject);
    if (!node) throw new Error('机关尚未加载：' + objective.text);
    const destination = questNodeApproach(node, 2);
    await approach(destination.point, destination.radius, owner);
    if (objective.puzzle === 'bq-repair') {
      const view = storyData().baiqiaoPuzzle;
      if (view?.mode !== 'story') throw new Error('机关台没有当前主线棋盘');
      const turns = solveBoard(view.board);
      if (!turns) throw new Error('机关台未找到有效连线');
      await storyRequest('puzzle-solve', { mode: 'story', level: view.level, turns }, owner);
    } else if (objective.puzzle === 'jz-catch') {
      if (!window.qingxiJianzhongCatch) throw new Error('试剑石场景尚未加载');
      const before = storyProgress(q.id);
      window.qingxiJianzhongCatch.open(node, { quests: () => window.__quests });
      const go = document.querySelector('.jz-stone [data-go]');
      if (!go || go.disabled) throw new Error('试剑石未准备好');
      go.click();
      const deadline = Date.now() + 90000;
      try {
        while (storyProgress(q.id) === before) {
          await storyReady(owner); catchVisibleSword();
          if (Date.now() > deadline) throw new Error('试剑石接剑未确认');
          await storyWait(80, owner);
        }
      } finally { if (owner === state.generation) document.querySelector('.jz-stone [data-close]')?.click(); }
    } else throw new Error('尚未识别的主线机关：' + objective.puzzle);
  }

  function livingEnemies(s) { return (s?.enemies || []).filter(e => e.hp > 0 && !e.hidden && !e.returning); }
  function fieldThreat(s) {
    if (!s?.overworld) return null;
    return livingEnemies(s).filter(e => e.engaged).sort((a,b) => dist(myPos(),a.position)-dist(myPos(),b.position))[0] || null;
  }
  function protectFieldHealth(s) {
    const ratio = s?.hp / Math.max(1,s?.hpMax);
    if (!s?.overworld || !(s.hp > 0) || !Number.isFinite(ratio) || ratio > .3 || !fieldThreat(s)) return;
    // 保留原生回血机会；血瓶用尽或冷却中时及时中止，交给外层正常回城。
    if (s.potions?.hp > 0 && s.cooldown?.hpPotion === 0 && act('hpPotion')) {
      state.stats.potions++; return;
    }
    recordDamage(s,performance.now());
    releaseKeys();
    if (s.active && s.state === 'fighting' && !s.paused) act('pause');
    const foe=fieldThreat(s), left=foe && foe.maxHp>0 ? '（'+foe.name+'剩余 '+Math.max(1,Math.round(foe.hp/foe.maxHp*100))+'% 气血）' : '';
    throw new Error('野外气血低于 30% 且无法立即回血'+left+'，已中止任务并尝试返回洞府');
  }
  function steerWalkable(heading, boundary = null, hazard = null) {
    const world = readWorld(), p = world?.actorPosition;
    if (!p || !Number.isFinite(heading)) { releaseKeys(); return; }
    const target = [p[0] + Math.sin(heading) * 3, p[1], p[2] + Math.cos(heading) * 3];
    const input = chooseWalkInput(world, target, fieldEnvironment, [], boundary, hazard);
    if (input) setKeys(input.keys); else releaseKeys();
  }
  function battleApproach(target, boundary, s) {
    const bounds = (Array.isArray(boundary) ? boundary : [boundary]).filter(Boolean).map(b => ({ ...b }));
    // 冲刺/击退可能把首领留在原生场外；只用场内余量接近，绝不越界退出副本。
    const arena = arenaInfo;
    if (!s.overworld && s.wave && arena && bounds.length === 1 &&
      dist(bounds[0].center, arena.center) < .1 && dist(target, arena.center) > arena.radius - 2) {
      bounds[0].radius = Math.max(bounds[0].radius, arena.radius - 1.5);
    }
    let point = target.slice();
    // 留出离散按键与一帧移动的余量，多重边界仍全部生效。
    for (let pass = 0; pass < 8; pass++) {
      let changed = false;
      for (const b of bounds) {
        const d = dist(point, b.center), radius = Math.max(0, b.radius - .75);
        if (d > radius) {
          point = [b.center[0] + (point[0] - b.center[0]) * radius / d, point[1],
            b.center[2] + (point[2] - b.center[2]) * radius / d];
          changed = true;
        }
      }
      if (!changed) break;
    }
    return { point, bounds };
  }
  // 场边滑行。旧逻辑进入边界缓冲带就掉头直奔场心：会穿进追过来的怪堆，而且一回到缓冲带以内又被走位
  // 逻辑推出去，人物在边线上原地折返、被近战怪追上。现在改为沿边界绕场：保持在边界内约 2.5 米的圆上，
  // 方向选怪少的一侧并至少保持 1 秒，另一侧明显更空才换向；走位本身要求向内或已回到场内时结束。
  function edgeSlide(me, bounds, want, enemies, now) {
    const old = state.edgeSlide, held = old && now - old.lastAt < 2500 ? old : null;
    const same = b => held && dist(b.center, held.center) < .1 && Math.abs(b.radius - held.radius) < .1;
    const bound = bounds.find(b => dist(me, b.center) > b.radius - 2) ||
      (held?.active && bounds.find(b => same(b) && dist(me, b.center) > b.radius - 4.5));
    if (!bound) { if (old) old.active = false; return null; }
    const inward = bearingTo(me, bound.center), r = dist(me, bound.center);
    const relative = Number.isFinite(want) ? norm(want - inward) : Math.PI;
    // 走位本身已经朝里走（目标在场内、追兵在外侧）且人还在边界以内：不干预。
    if (Math.abs(relative) < 1 && r <= bound.radius) { if (old) old.active = false; return null; }
    const slide = same(bound) ? held : (state.edgeSlide = {center:bound.center.slice(), radius:bound.radius, side:0, since:now, lastAt:now, active:true});
    const clearance = side => {
      let best = Infinity;
      for (const ahead of [3, 6]) {
        const heading = inward + side * Math.PI / 2;
        const probe = [me[0] + Math.sin(heading) * ahead, 0, me[2] + Math.cos(heading) * ahead];
        for (const e of enemies) best = Math.min(best, dist(probe, e.position));
      }
      return best;
    };
    const left = clearance(1), right = clearance(-1);
    if (!slide.side) {
      const prefer = Math.abs(Math.sin(relative)) > .05 ? Math.sign(Math.sin(relative)) : state.orbitDir;
      slide.side = Math.abs(left - right) > 1 ? (left > right ? 1 : -1) : prefer; slide.since = now;
    } else if (now - slide.since > 1000 && (slide.side > 0 ? right - left : left - right) > 2.5) {
      slide.side = -slide.side; slide.since = now; slide.anchor = null;
    }
    // 这一侧被山石、围栏挡住走不动（0.6 秒位移不足 1.2 米）就换另一侧，不顶着障碍等怪追上来。
    if (!slide.anchor || now - slide.anchorAt > 2000) { slide.anchor = me.slice(); slide.anchorAt = now; }
    else if (now - slide.anchorAt >= 600) {
      if (dist(me, slide.anchor) < 1.2) { slide.side = -slide.side; slide.since = now; }
      slide.anchor = me.slice(); slide.anchorAt = now;
    }
    slide.active = true; slide.lastAt = now;
    // 以边界内 2.5 米为目标半径：偏外向内收，偏内略向外放，稳定地贴着这条圆走。
    return inward + slide.side * Math.atan2(1, Math.max(-.6, Math.min(3, (r - (bound.radius - 2.5)) / 1.2)));
  }
  function storyBattleFrame(s, target, boundary, outputEnabled = true) {
    const me = readWorld()?.actorPosition, now = performance.now();
    if (!me || s.paused || !target) { releaseKeys(); return; }
    recordDamage(s, now);
    catchVisibleSword();
    const enemies = livingEnemies(s).filter(e => !s.overworld || e.engaged || e.id === target.id || dist(me,e.position)<CFG.retreatDistance);
    const relevant = { ...s, enemies, target:target.id };
    let heading;
    const d = dist(me, target.position), step = steeringTarget(relevant, me, now);
    if (outputEnabled && moonCombatFrame(s,target,step,boundary,now)) return;
    yufengObserve(target);
    if (outputEnabled && yufengBurst(s,target,step,now)) {
      attack(s,null,now,true,true);
      state.navigation={mode:'casting',distance:d,reason:'安全空档站定施法，预警出现即恢复躲避',updatedAt:Date.now()};
      return;
    }
    const approach = battleApproach(target.position, boundary, s), bounds = approach.bounds;
    const pursuing = !step.threat?.hazard && !step.retreat && d > 25;
    if (pursuing) {
      // 旧逻辑在场边反复向敌人/场心折返，场外目标永远进不了点杀射程。
      const remaining = dist(me, approach.point);
      heading = remaining > .7 ? bearingTo(me, approach.point) : null;
      state.navigation = { mode:'combat-approach', target:target.id, distance:d,
        goal:approach.point.slice(), remaining, inRange:d < 32,
        reason:remaining > .7 ? '接近场内攻击位置' : d < 32 ? '场内边缘持续远程输出' : '目标在原生场外且超出射程', updatedAt:Date.now() };
    } else {
      const slide = step.threat?.hazard ? null : edgeSlide(me, bounds, step.want, enemies, now);
      heading = slide ?? step.want;
      if (state.navigation?.mode === 'combat-approach') state.navigation = null;
    }
    // 首要威胁之外的困阵 / 火莲圈同样不能走进去（追击、绕行、场边滑行都受约束）。
    const hazards = [step.threat?.hazard, ...groundHazards()].filter(Boolean);
    if (Number.isFinite(heading)) steerWalkable(heading, bounds, hazards.length ? hazards : null);
    else releaseKeys();
    const rolled = dodgeThreat(s, step, now);
    if (!outputEnabled) { attack(s, null, now, true, true); return; }
    const ultimate = !rolled && !step.critical && !step.mobile && castUltimate(s, { e: target, d }, now);
    if (s.target !== target.id && now - state.lastLockAt > 450) { state.lastLockAt = now; act('lock'); return; }
    const output = attack(ultimate || rolled ? readCombat() : s, { e: target, d }, now, step.critical, step.mobile);
    if (output !== 'none') { state.lastAct = output; log('输出 ' + output); }
    // 没有飞剑时仅用远程法术；撤离时不为兜底输出停步。
    if (output === 'none' && target.role!=='yufeng_mu' && !step.dodging && !step.retreat && !step.mobile) {
      if (d < 24 && s.cooldown?.spell === 0 && s.mana >= 15) act('spell');
    }
  }
  function fieldRejectionHint(row) {
    const hint=hitAuditText();
    return (Number.isFinite(row?.hits)?' · 上报命中 '+row.hits+' 次':'')+(hint?' · '+hint:'');
  }
  async function fightEnemy(enemyId, owner) {
    if (/^yfc_mu/.test(String(enemyId))) warnHitInflation('穆姓上师');
    const deadline = Date.now() + 5 * 60000;
    const receiptStart=fieldSettlementSequence,spawnIds=new Set([enemyId]);
    const checkSettlement=()=>{
      const rejected=fieldSettlements.find(r=>r.sequence>receiptStart && spawnIds.has(r.enemyId) && !r.ok && r.error==='quest_enemy_combat_invalid');
      if(!rejected)return;
      releaseKeys();const s=readCombat();
      if(s?.active && s.state==='fighting' && !s.paused)act('pause');
      throw new Error('击杀结算被服务端拒绝：'+rejected.enemyId+' · '+rejected.error+fieldRejectionHint(rejected)+'；已停止自动重打并尝试返回洞府');
    };
    let refresh = null, waitingAt = null, previousPhase = state.phase;
    try {
      while (true) {
        storyCheck(owner);checkSettlement();
        await storyReady(owner);checkSettlement();
        const s = readCombat();
        protectFieldHealth(s);
        // 击杀回执和资料刷新都可能延迟；原目标倒下后仍处理其他追击怪。
        const target = livingEnemies(s).find(e => e.id === enemyId) || fieldThreat(s);
        if (!target) {
          releaseKeys();
          waitingAt ??= Date.now();
          if (!s?.fieldPending && !refresh) {
            refresh = { done:false, error:null };
            const quests = window.__quests;
            Promise.resolve().then(() => { storyCheck(owner); return quests.refresh(false); }).then(
              () => { refresh.done = true; },
              error => { refresh.error = error; refresh.done = true; }
            );
          }
          if (!s?.fieldPending && refresh?.done) {
            if (refresh.error) throw refresh.error;
            storyCheck(owner); return;
          }
          if (Date.now() > deadline || Date.now()-waitingAt > CFG.settlementTimeoutMs) throw new Error('击杀记录同步超时，已中止任务并尝试返回洞府');
          const message = s?.fieldPending ? '等待击杀记录同步，保持警戒' : '等待任务资料同步，保持警戒';
          if (state.phase !== message) step(message);
          state.navigation = { mode:'syncing',distance:0,reason:'附近敌人追击时立即反击',updatedAt:Date.now() };
          await storyWait(CFG.tickMs, owner); continue;
        }
        spawnIds.add(target.id);if(target.spawnId)spawnIds.add(target.spawnId);
        if (Date.now() > deadline) throw new Error('战斗超时：' + target.name);
        const message = '野外反击：' + target.name;
        if (state.phase !== message) step(message);
        state.navigation = {mode:'combat',distance:dist(myPos(),target.position),reason:'移动避敌并反击',updatedAt:Date.now()};
        const home = target.home ? { center: target.home, radius: Math.max(5, (target.leashRadius || 28) - 4) } : null;
        const arena = storyData()?.field?.enemies?.find(e => e.id === target.spawnId)?.arena;
        const region = storyData()?.field?.regions?.[arena];
        const zone = region ? { center:position3(region.center), radius:Math.max(3,region.radius-4) } : null;
        storyBattleFrame(s, target, [home,zone].filter(Boolean));
        await storyWait(CFG.tickMs, owner);
      }
    } finally {
      if (owner === state.generation) {
        releaseKeys(); state.navigation = null;
        if (state.phase.startsWith('野外反击：') || state.phase.startsWith('等待击杀记录同步') || state.phase.startsWith('等待任务资料同步')) step(previousPhase);
      }
    }
  }
  async function defendNearby(owner) {
    const target = fieldThreat(readCombat());
    if (!target) return false;
    await fightEnemy(target.id, owner); return true;
  }
  async function pickupNeededCorpses(itemId, owner) {
    const corpses = (storyData()?.corpses || []).filter(c => !c.looted && (!c.expiresAt || c.expiresAt > Date.now() / 1000) &&
      (!c.region || c.region === storyData().world?.current) && (c.items || []).some(i => i.itemId === itemId && i.quantity > 0));
    const corpse = corpses.sort((a, b) => dist(myPos(), position3(a.position)) - dist(myPos(), position3(b.position)))[0];
    if (!corpse) return false;
    await walkTo(position3(corpse.position), 2.2, owner);
    const opened = await storyRequest('corpse-open', { corpseId: corpse.id }, owner);
    if (opened.lootWindow?.items?.length) await storyRequest('corpse-pickup', { corpseId: corpse.id }, owner);
    return true;
  }
  async function fieldObjective(q, objective, owner) {
    const rejected=fieldSettlements.find(r=>r.sequence>(state.story.fieldReceiptStart??fieldSettlementSequence) && !r.ok && r.error==='quest_enemy_combat_invalid');
    if(rejected)throw new Error('击杀结算被服务端拒绝：'+rejected.enemyId+' · '+rejected.error+fieldRejectionHint(rejected)+'；已停止自动重打并尝试返回洞府');
    // 等待掉落怪刷新时也要处理已靠近的敌人，不能站着等待服务端旧时间。
    if (await defendNearby(owner)) {
      if (objective.itemId) await pickupNeededCorpses(objective.itemId, owner);
      return;
    }
    if (objective.itemId && await pickupNeededCorpses(objective.itemId, owner)) return;
    const dropped = (storyData()?.nodes || []).find(n => n.itemId === objective.itemId && n.id.startsWith('loot:'));
    if (dropped) { await gatherNode(dropped, owner); return; }
    const role = objective.enemyRole || objective.sourceEnemyRole;
    const roster = (storyData()?.field?.enemies || []).filter(e => e.arena === objective.arena && e.role === role);
    const alive = new Set(livingEnemies(readCombat()).map(e => e.spawnId).filter(Boolean));
    const candidates = roster.filter(e => (e.respawnAt || 0) <= Date.now() / 1000 || alive.has(e.id));
    candidates.sort((a, b) => dist(myPos(), position3(a.position)) - dist(myPos(), position3(b.position)));
    const spawn = candidates[0];
    if (!spawn) {
      const next = roster.slice().sort((a,b)=>(a.respawnAt||0)-(b.respawnAt||0))[0];
      const remaining = next ? Math.max(0,Math.ceil(next.respawnAt-Date.now()/1000)) : null;
      const message = next ? '等待'+next.name+'刷新'+(remaining ? ' · 约 '+remaining+' 秒' : '') : '等待任务敌人出现';
      step(q.title+' · '+message); releaseKeys();
      state.navigation = { mode:'waiting', position:myPos().slice(), goal:next ? position3(next.position) : myPos().slice(),
        distance:next ? dist(myPos(),position3(next.position)) : 0, keys:'', flight:null, reason:message, updatedAt:Date.now() };
      await storyWait(3000, owner); await window.__quests.refresh(false); storyCheck(owner); return;
    }
    step(q.title + ' · ' + objective.text);
    if (!readCombat()?.overworld) {
      const region = storyData()?.field?.regions?.[objective.arena];
      if (!region) throw new Error('任务战斗区域尚未就绪：'+objective.arena);
      // 敌人的射程圈可能跨出区域；游戏开启战斗要求人物位于整个区域内部。
      await approach(position3(region.center), Math.max(3,region.radius-4), owner);
      if (!window.__rt.startQuestCombat?.(objective.arena)) throw new Error('游戏未接受郊外战斗入口，请确认人物已进入区域');
    }
    // 在点杀射程内交战，避免为攻击目标强行走到近处。
    await approach(position3(spawn.position), Math.min(26, CFG.swordRange - 3), owner);
    await until(() => livingEnemies(readCombat()).some(e => e.spawnId === spawn.id), 60000, '郊外敌人模型', owner);
    const enemy = livingEnemies(readCombat()).find(e => e.spawnId === spawn.id);
    if (enemy) await fightEnemy(enemy.id, owner);
    if (objective.itemId) await pickupNeededCorpses(objective.itemId, owner);
  }
  const INSTANCE_LABELS = { moonpalace: '月殿血誓 · 单人副本', bloodland: '血色禁地 · 单人副本', southgate: '南门守城 · 单人副本',
    escort: '护民出关 · 单人副本', mulanshrine: '血祭神坛 · 单人副本',
    bqpuppet: '试傀场 · 单人副本', jianzhong: '剑冢 · 单人副本' };
  const ESCORT_ROUTE = [[-5,128],[-5,200],[-8,232],[-7,290],[-9,330],[-7,410],[-8,450],[-6,505]];
  const SWORD_FLOORS = [{ point: [6500,16,-4214], radius: 15 }, { point: [6500,36,-4284], radius: 14 }, { point: [6500,60,-4356], radius: 13 }];
  function escortPoint(distance) {
    for (let i = 1; i < ESCORT_ROUTE.length; i++) {
      const a = ESCORT_ROUTE[i - 1], b = ESCORT_ROUTE[i], length = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (distance <= length) return [a[0] + (b[0] - a[0]) * distance / length, 0, a[1] + (b[1] - a[1]) * distance / length];
      distance -= length;
    }
    return position3(ESCORT_ROUTE.at(-1));
  }
  function instanceResult(s, id) { return id === 'moonpalace' ? s?.moonPalace : id === 'bloodland' ? s?.bloodLand : id === 'southgate' ? s?.southGate : id === 'escort' ? s?.escort : s?.contentRun; }
  // 月殿血誓上报的 hits 是玩家命中次数（metrics.playerHits），野外首领击杀上报该怪被命中次数；
  // 月殿已多次被服务端以 count_cap:hits 判为 review。紫罗极火（qianlan）的被动冰焰按原生注释给 38% 的直接命中
  // 追加 2.5 秒、每 0.5 秒一跳的持续伤害，每一跳都计一次命中，推算约 1+0.38×5=2.9 倍；实测一局为 1751 / 飞剑 577。
  // 服务端上限未知，仍有飞剑命中占比很高的拒绝记录；被动不是唯一原因。这里只读原生快照做提示，不改上报。
  function hitAudit(combat = readCombat()) {
    const metrics=combat?.metrics, slot=(combat?.kit || []).find(k=>k.skill==='qianlan' && !k.locked);
    const player=Number.isFinite(metrics?.playerHits)?metrics.playerHits:null, sword=Number.isFinite(metrics?.swordHits)?metrics.swordHits:null;
    return {playerHits:player,swordHits:sword,ratio:player!=null && sword>0?Math.round(player/sword*100)/100:null,passive:slot?.name || null};
  }
  // 月殿逐局本地诊断：只白名单读取原生状态，不保存登录凭据、不发送额外请求、不改战斗状态。
  const MOON_DIAGNOSTIC_KEY='dwelling-autobot.moon-diagnostics';
  const MOON_DIAGNOSTIC_LIMIT=700;
  let moonDiagnosticHistory=[],moonDiagnosticContext=null,moonDiagnosticStorageError=false;
  const moonDiagnosticOwners=new Map();
  try {
    const saved=JSON.parse(localStorage.getItem(MOON_DIAGNOSTIC_KEY)||'[]');
    if (Array.isArray(saved)) moonDiagnosticHistory=saved.filter(run=>run && typeof run.id==='string' && Array.isArray(run.events))
      .slice(-3).map(run=>({...run,events:run.events.slice(-MOON_DIAGNOSTIC_LIMIT)}));
  } catch {}
  if (sameScene && inherited?.moonDiagnostics) {
    moonDiagnosticHistory=inherited.moonDiagnostics.history;moonDiagnosticContext=inherited.moonDiagnostics.context;
  }
  function persistMoonDiagnostics() {
    try { localStorage.setItem(MOON_DIAGNOSTIC_KEY,JSON.stringify(moonDiagnosticHistory));moonDiagnosticStorageError=false; }
    catch { moonDiagnosticStorageError=true; }
  }
  const moonNumber=value=>Number.isFinite(value)?Math.round(value*1000)/1000:null;
  const moonPoint=value=>{
    const p=Array.isArray(value)?value:value && [value.x,value.y,value.z];
    return p?.length===3 && p.every(Number.isFinite)?p.map(moonNumber):null;
  };
  function moonCounts(counts) {
    if (!counts || typeof counts!=='object') return null;
    return Object.fromEntries(['runeRounds','runeTimeouts','channels','interrupts','mirrorsBroken','mirrorReflects','bladeHits','hazardHits']
      .map(key=>[key,moonNumber(counts[key])]));
  }
  function moonDiagnosticSnapshot(s) {
    let native=null,targets=[];
    try { native=window.qingxiNgqBoss?.stats?.(); } catch {}
    try { targets=(window.__fc?.mechanicInteractions?.()||[]).filter(t=>t.data?.kind==='boss-rune').slice(0,3); } catch {}
    const position=moonPoint(readWorld()?.actorPosition),boss=s?.enemies?.find(e=>e.role==='nangongque')||s?.enemies?.find(e=>e.alive!==false);
    return {observedAt:Date.now(),seconds:moonNumber(s?.wave?.time),state:s?.state||null,paused:!!s?.paused,hidden:!!document.hidden,
      blocked:!!window.__rt?.mobileHudState?.()?.blocked,position,hp:moonNumber(s?.hp),mana:moonNumber(s?.mana),
      boss:boss?{state:boss.state||null,phase:boss.phase||null,hp:moonNumber(boss.hp),position:moonPoint(boss.position)}:null,
      mode:native?.mode||null,phase:moonNumber(native?.phase),channel:moonNumber(native?.channel),pools:moonNumber(native?.pools),counts:moonCounts(native?.counts),
      runes:Array.isArray(native?.runes)?native.runes.slice(0,3).map(value=>String(value).slice(0,20)):null,
      targets:targets.map(t=>{const point=moonPoint(t.point);return {id:String(t.data.id).slice(0,64),point,radius:moonNumber(t.radius),
        distance:position&&point?moonNumber(Math.hypot(position[0]-point[0],position[2]-point[2])):null,
        distance3d:position&&point?moonNumber(dist(position,point)):null};}),
      keys:[...state.heldKeys],casting:typeof s?.casting==='string'?s.casting.slice(0,80):s?.casting?
        {name:s.casting.name||null,kind:s.casting.kind||null,left:moonNumber(s.casting.left),remaining:moonNumber(s.casting.remaining)}:null,
      gcd:moonNumber(s?.gcdLeft),sampleGapMs:moonDiagnosticContext?.run?.last?Math.max(0,Date.now()-moonDiagnosticContext.run.last.observedAt):null};
  }
  function appendMoonDiagnostic(run,type,sample,detail={}) {
    if (!run) return;
    const event={at:Date.now(),type,sample,...detail};
    run.events.push(event);
    if (run.events.length>MOON_DIAGNOSTIC_LIMIT) {run.events.shift();run.dropped=(run.dropped||0)+1;}
    // 完整采样只留本机；控制台也留下结构化事件，便于人工在开发工具中核对。
    if (type!=='sample' && type!=='rune_walk') console.log('[月殿诊断] '+JSON.stringify(event));
  }
  function observeMoonDiagnostics(s=readCombat(),type=null,detail={}) {
    try {
      const now=Date.now(),context=moonDiagnosticContext;
      const inMoon=s?.arena==='moonpalace' && s.active;
      if (!inMoon) {
        if (context) {
          appendMoonDiagnostic(context.run,'scene_left',context.run.last,{note:'保留退出前快照；不读取洞府新实例的零计数'});
          context.run.endedAt=now;context.run.endReason='scene_left';moonDiagnosticContext=null;persistMoonDiagnostics();
        }
        return null;
      }
      const sample=moonDiagnosticSnapshot(s),nativeRunId=combatRunId(s);
      const newRun=!context || context.runtime!==window.__fc ||
        nativeRunId && context.nativeRunId && nativeRunId!==context.nativeRunId ||
        sample.seconds!=null && context.run.last?.seconds!=null && sample.seconds<context.run.last.seconds-.5 ||
        context.run.last?.state!=='fighting' && sample.state==='fighting';
      if (newRun) {
        if (context) {context.run.endedAt=now;context.run.endReason='next_run';appendMoonDiagnostic(context.run,'run_replaced',context.run.last);}
        const run={id:'moon-'+now.toString(36)+'-'+Math.random().toString(36).slice(2,7),version:'2.0.78',startedAt:now,events:[],dropped:0};
        moonDiagnosticHistory.push(run);moonDiagnosticHistory=moonDiagnosticHistory.slice(-3);
        moonDiagnosticContext={run,runtime:window.__fc,nativeRunId,signature:null,lastSampleAt:0,finaleAt:null,finaleSeconds:null,lastWalkAt:0};
        appendMoonDiagnostic(run,'run_start',sample,{note:sample.seconds>1?'中途接管，不能还原接管前的轨迹':'开始记录'});
      }
      const current=moonDiagnosticContext,run=current.run,previous=run.last;
      if (nativeRunId) {
        current.nativeRunId=nativeRunId;moonDiagnosticOwners.set(nativeRunId,run);
        if (moonDiagnosticOwners.size>12) moonDiagnosticOwners.delete(moonDiagnosticOwners.keys().next().value);
      }
      if (sample.mode==='finale' && (previous?.mode!=='finale' || sample.counts?.runeRounds!==previous?.counts?.runeRounds)) {
        current.finaleAt=now;current.finaleSeconds=sample.seconds;appendMoonDiagnostic(run,'rune_round_start',sample);
      }
      if (sample.mode==='finale') {
        sample.finaleObservedMs=current.finaleSeconds!=null && sample.seconds!=null?Math.max(0,Math.round((sample.seconds-current.finaleSeconds)*1000)):null;
        sample.finaleWallMs=Math.max(0,now-current.finaleAt);
      }
      if (previous && sample.mode!==previous.mode) appendMoonDiagnostic(run,'mode_change',sample,{from:previous.mode,to:sample.mode});
      if (previous && (sample.counts?.runeTimeouts??0)>(previous.counts?.runeTimeouts??0)) {
        appendMoonDiagnostic(run,'rune_timeout',sample,{before:previous,attempt:run.lastAttempt||null});
        run.timeouts=sample.counts.runeTimeouts;
      }
      if (previous && JSON.stringify(sample.runes)!==JSON.stringify(previous.runes)) appendMoonDiagnostic(run,'rune_state',sample,{beforeRunes:previous.runes});
      if (sample.runes?.length===3 && sample.runes.every(value=>value==='sealed') && !previous?.runes?.every(value=>value==='sealed'))
        appendMoonDiagnostic(run,'runes_complete',sample,{note:'仅依据原生三枚 sealed 确认'});
      if (previous?.hp!=null && sample.hp!=null && sample.hp<previous.hp) appendMoonDiagnostic(run,'damage',sample,{lost:moonNumber(previous.hp-sample.hp)});
      if (previous?.boss?.hp!=null && sample.boss?.hp>previous.boss.hp) appendMoonDiagnostic(run,'boss_heal',sample,{healed:moonNumber(sample.boss.hp-previous.boss.hp)});
      const signature=JSON.stringify([sample.state,sample.mode,sample.paused,sample.hidden,sample.blocked,sample.counts,sample.targets.map(t=>t.id)]);
      if (signature!==current.signature || now-current.lastSampleAt>=(sample.mode==='finale'?500:4000)) {
        appendMoonDiagnostic(run,'sample',sample);current.signature=signature;current.lastSampleAt=now;
      }
      run.last=sample;
      if (sample.counts && Object.values(sample.counts).some(Number.isFinite)) run.lastNative=sample;
      if (type) appendMoonDiagnostic(run,type,sample,detail);
      const changed=previous && (sample.mode!==previous.mode || JSON.stringify(sample.runes)!==JSON.stringify(previous.runes) ||
        JSON.stringify(sample.counts)!==JSON.stringify(previous.counts));
      if (type==='settlement' || type==='rune_walk_error' || newRun || changed || now-(current.persistedAt||0)>=1000) {persistMoonDiagnostics();current.persistedAt=now;}
      return run;
    } catch { return null; } // 诊断失败不能阻断原来的移动、交互或结算。
  }
  function moonDiagnosticRequest(action,body) {
    try {
      if (action!=='end') return null;
      const report=body?.report,submitted={result:['victory','defeat','quit'].includes(body?.result)?body.result:null,
        seconds:moonNumber(report?.seconds),alive:typeof report?.alive==='boolean'?report.alive:null,
        mechanics:{...moonCounts(report?.mechanics),hits:moonNumber(report?.mechanics?.hits)}};
      // quit 的 end 请求可能晚于场景退出，按原生 runId 找回所属局，不能记进新的一局。
      const context=moonDiagnosticContext,previous=context?.runtime===window.__fc &&
        (!context.nativeRunId || context.nativeRunId===body?.runId)?context?.run:null;
      const live=observeMoonDiagnostics(),run=moonDiagnosticOwners.get(body?.runId)||previous||(!body?.runId?live:null);
      if (run) {
        if (submitted.mechanics.runeTimeouts>(run.lastNative?.counts?.runeTimeouts??0))
          appendMoonDiagnostic(run,'rune_timeout',run.last,{source:'native_report',submitted,note:'原生上报确认了采样间隔内发生的镇纹超时；位置仍为最后采样位置'});
        run.submitted=submitted;appendMoonDiagnostic(run,'submit',run.last,{submitted});persistMoonDiagnostics();
      }
      return run;
    } catch { return null; }
  }
  function moonDiagnosticResponse(run,data,error=false) {
    try {
      if (!run) return;
      const receipt={kind:typeof data?.kind==='string'?data.kind.slice(0,32):null,
        flags:Array.isArray(data?.flags)?data.flags.filter(f=>typeof f==='string').slice(0,10).map(f=>f.slice(0,80)):[],
        error:error?'原生请求抛出异常':typeof data?.error==='string'?safeLogText(data.error,120):null};
      run.receipt=receipt;appendMoonDiagnostic(run,'receipt',run.last,{receipt});persistMoonDiagnostics();
    } catch { return null; }
  }
  function moonDiagnostics() {
    return {schema:1,storageError:moonDiagnosticStorageError,runs:JSON.parse(JSON.stringify(moonDiagnosticHistory))};
  }
  function moonDiagnosticSummary() {
    const run=moonDiagnosticHistory.at(-1),sample=run?.lastNative||run?.last;
    if (!run) return '月殿日志：暂无本版记录';
    const counts=run.submitted?.mechanics||sample?.counts,number=value=>Number.isFinite(value)?value:'未知';
    const sealed=sample?.runes?.filter(value=>value==='sealed').length;
    return '月殿'+(moonDiagnosticContext?'本局':'上局')+'留档：镇纹 '+(sealed==null?'—':sealed+' / 3')+
      ' · 轮数 '+number(counts?.runeRounds)+' · 超时 '+number(counts?.runeTimeouts)+
      ' · 血契 '+number(counts?.interrupts)+' / '+number(counts?.channels)+' · 飞剑伤 '+number(counts?.bladeHits)+
      ' · 场地伤 '+number(counts?.hazardHits)+' · 日志 '+run.events.length+' 条'+(moonDiagnosticStorageError?'（存储失败，仅内存）':'');
  }
  // 独立观察器覆盖暂停、手动操作及等待结算；镇纹寻路还会在每次决策处额外采样。
  if (window.__dwellingMoonDiagnosticTimer) clearInterval(window.__dwellingMoonDiagnosticTimer);
  window.__dwellingMoonDiagnosticTimer=setInterval(()=>observeMoonDiagnostics(),500);

  // 每日副本每一局的结算摘要，跨刷新保留最近 40 条（助手日志只留最近 200 行，事后查不到）。
  const INSTANCE_HISTORY_KEY='dwelling-autobot.instance-history';
  let instanceHistory=[];
  try {
    const saved=JSON.parse(localStorage.getItem(INSTANCE_HISTORY_KEY) || '[]');
    if (Array.isArray(saved)) instanceHistory=saved.filter(row=>row && typeof row==='object').slice(-40);
  } catch (e) {}
  function recordInstanceRun(id, combat = readCombat(), extra = {}) {
    if (!INSTANCE_LABELS[id] || combat?.arena!==id) return null;
    const view=instanceResult(combat,id), result=view?.result, audit=hitAudit(combat);
    const row={at:Date.now(),version:window.__autobot?.version || null,id,state:combat.state || null,kind:result?.kind || null,
      flags:Array.isArray(result?.flags)?result.flags.slice(0,6):[],rewarded:view?.run?.rewarded ?? null,
      seconds:Number.isFinite(result?.elapsed)?result.elapsed:Number.isFinite(combat.wave?.time)?Math.round(combat.wave.time):null,
      playerHits:audit.playerHits,swordHits:audit.swordHits,passive:audit.passive,
      hp:Number.isFinite(combat.hp)?Math.round(combat.hp):null,hpMax:combat.hpMax ?? null,potions:combat.potions?{...combat.potions}:null,...extra};
    // 战败时留下最后几次受伤的来源，便于判断是哪一招没躲开。
    if (combat.state==='defeat') row.damage=state.damage.slice(-6).map(d=>({lost:Math.round(d.hpBefore-d.hp),hp:Math.round(d.hp),
      enemy:d.enemies?.[0]?{phase:d.enemies[0].phase,move:d.enemies[0].move,state:d.enemies[0].state,distance:Math.round(d.enemies[0].distance)}:null}));
    if (id==='moonpalace') {
      const diagnostic=observeMoonDiagnostics(combat,'settlement',{receipt:{kind:row.kind,flags:row.flags,state:row.state}});
      if (diagnostic) {
        row.moonDiagnosticId=diagnostic.id;
        row.moon={counts:diagnostic.lastNative?.counts||null,runes:diagnostic.lastNative?.runes||null,
          submitted:diagnostic.submitted||null};
      }
    }
    instanceHistory.push(row);
    if (instanceHistory.length>40) instanceHistory.splice(0,instanceHistory.length-40);
    try { localStorage.setItem(INSTANCE_HISTORY_KEY,JSON.stringify(instanceHistory)); } catch (e) {}
    return row;
  }
  function hitAuditText(audit = hitAudit()) {
    return audit.passive?'已装备「'+audit.passive+'」，被动冰焰的每一跳都计入上报命中数（约放大到 2.9 倍）；命中记录仅用于诊断，不按累计次数停火':'';
  }
  function warnHitInflation(label) {
    const text=hitAuditText(); if (!text) return;
    const key=label+'|'+text; if (state.hitWarning===key) return;
    state.hitWarning=key; log(label+'：'+text);
  }
  // ---- 可选月殿换装（默认关闭）：手动自检或显式开启时收起冰焰，结束后祭回 ----
  // 原生 damageEnemy 每调用一次 metrics.playerHits 加一，冰焰的每一跳（dot）也算；qianlanProc 给 38% 的直接命中
  // 挂 2.5 秒、每 0.5 秒一跳且可叠加的冰焰。被动只看法宝是否在技能位（slotEffect 遍历 profile.slots），
  // 所以只能收宝。这里只调用储物袋「收宝 / 祭出」按钮背后的页面函数 operateTreasure，技能位顺序用
  // 页面的 saveDoufaSkillSlots 还原；先把待恢复记录写入本地存储，刷新、停止或出错后都能接着装回。
  const LOADOUT_KEY='dwelling-autobot.loadout-restore';
  function doufaProfile() {
    try {
      const profile=window.__rt?.mobileHudState?.()?.profile;
      return Array.isArray(profile?.slots) ? profile : null;
    } catch (e) { return null; }
  }
  function hitPassiveSlots(profile = doufaProfile()) {
    return (profile?.slots || []).filter(slot=>slot?.itemId && !slot.locked &&
      (slot.skill==='qianlan' || Number(slot.effects?.qianlan_proc_chance_pct)>0));
  }
  function slotOrder(profile = doufaProfile()) {
    return (profile?.slots || []).slice().sort((a,b)=>a.slot-b.slot).map(slot=>slot.itemId);
  }
  // 被动只对未锁定的技能位生效（原生 slotEffect 过滤 locked），收起后留一个锁定的空位也算收起。
  function slotActive(itemId, profile = doufaProfile()) {
    return (profile?.slots || []).some(slot=>slot?.itemId===itemId && !slot.locked);
  }
  function loadoutPlayer() {
    try { return typeof window.ownWorldIdentity==='function' ? String(window.ownWorldIdentity()) : rewardBridge?.player_id || null; }
    catch (e) { return rewardBridge?.player_id || null; }
  }
  function saveLoadoutMarker(marker, required = false) {
    try { localStorage.setItem(LOADOUT_KEY,JSON.stringify(marker)); }
    catch (e) { if (required) return false; }
    state.loadout=marker;
    return true;
  }
  try {
    const saved=JSON.parse(localStorage.getItem(LOADOUT_KEY) || 'null');
    if (saved && Array.isArray(saved.items) && saved.items.every(item=>item && typeof item.itemId==='string') && Array.isArray(saved.order))
      state.loadout={...saved,hold:false,restoring:false};
  } catch (e) {}
  function instanceCombatActive(combat = readCombat()) { return !!combat?.active && !combat.overworld; }
  // 让页面用自己的入口重读一次斗法档案（只读）。入口不存在、页面正忙或迟迟不返回都不影响后续判断。
  async function refreshDoufaProfile() {
    const refresh=window.refreshLiveDoufaProfile;
    if (typeof refresh!=='function') return;
    let timer;
    try { await Promise.race([Promise.resolve().then(()=>refresh()),new Promise(resolve=>{timer=setTimeout(resolve,8000);})]); }
    catch (e) {}
    finally { clearTimeout(timer); }
  }
  function pageToast() {
    try { return safeLogText(document.getElementById('toast')?.textContent || '',60); } catch (e) { return ''; }
  }
  // 页面正忙时原生函数直接返回 false；每次调用后只按技能位里是否还有这件法宝判断成败。
  async function treasureAction(action, item, done, check = () => {}) {
    const native=window.operateTreasure;
    if (typeof native!=='function') throw new Error('页面没有法宝收宝 / 祭出入口');
    let failure='';
    for (let attempt=0; attempt<3; attempt++) {
      check();
      if (instanceCombatActive()) throw new Error('副本进行中，不能调整法宝');
      let timer, ok=false;
      const before=pageToast();
      try {
        ok=await Promise.race([Promise.resolve().then(()=>native(action,item.itemId)),
          new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('请求超时')),25000);})]);
      } catch (error) { failure=error?.message || String(error); }
      finally { clearTimeout(timer); }
      // 原生函数被服务端拒绝时只弹提示、不抛错：把这一次新弹出的提示记作原因，便于事后判断为什么没收成。
      if (ok!==true) { const said=pageToast(); if (said && said!==before) failure=said; }
      // 档案丢失时 !slotActive 也会为真，不能据此声称收宝成功。
      for (let i=0; i<10; i++) { check(); if (doufaProfile() && done()) return true; await sleep(300); }
      // 请求可能已在服务端生效，只是页面随后读档案失败（原生函数这时同样返回 false）：先重读档案再判断，
      // 不凭旧档案重复收 / 祭，也不把已经收起的法宝当成没收成而漏掉祭回。
      await refreshDoufaProfile(); check();
      if (!doufaProfile()) throw new Error('斗法档案未载入，无法确认法宝状态');
      if (done()) return true;
    }
    throw new Error((action==='withdraw'?'收起':'祭出')+'「'+item.name+'」未生效'+(failure?'：'+failure:''));
  }
  // 页面有法宝入口，但斗法档案和战斗快照里的技能位都还读不到（刚进场景、网络慢）：技能位未知，不等于没有这类法宝。
  function loadoutUnknown() {
    return typeof window.operateTreasure==='function' && !doufaProfile() && !(readCombat()?.kit || []).length;
  }
  function moonEntryIssue(swap) {
    if (!CFG.moonSkipHitPassive) return null;
    const passive=hitAudit().passive || hitPassiveSlots()[0]?.name || null;
    if (passive) return {code:'hit_passive',reason:'「'+passive+'」仍在技能位'+
      (swap?.skip?'（自动收起未成功：'+swap.skip+'）':'')+'，被动冰焰的每一跳都计入命中数；本场未进入、未消耗今日机会，卸下后再运行'};
    const unknown=swap?.unknown || loadoutUnknown();
    if (unknown || swap?.skip) return {code:unknown?'loadout_unknown':'loadout_unconfirmed',
      reason:(swap?.skip || '斗法档案未载入，无法确认技能位里有没有放大命中数的被动法宝')+'；本场未进入、未消耗今日机会，稍后再运行'};
    return null;
  }
  // 返回 {changed:false}（没有这类法宝）、{changed:true,names}（已收起）或 {skip:原因}（没收成，已尽量装回）；
  // 技能位读不到时返回 {skip,unknown:true}。
  async function stripHitPassives(owner) {
    const check=()=>checkOwner(owner);
    if (state.loadout) await restoreLoadout();
    if (loadoutUnknown()) {
      step('月殿换装：等待斗法档案');
      await refreshDoufaProfile();
      for (let i=0; i<20 && loadoutUnknown(); i++) { check(); await sleep(300); }
      check();
      if (loadoutUnknown()) return {skip:'斗法档案未载入，无法确认技能位里有没有放大命中数的被动法宝',unknown:true};
    }
    const profile=doufaProfile(), targets=hitPassiveSlots(profile);
    if (!targets.length) return hitAudit().passive ? {skip:'读不到斗法档案里的法宝编号'} : {changed:false};
    if (state.loadout) return {skip:'上一次收起的法宝还没有装回'};
    if (instanceCombatActive()) return {skip:'已在副本内，不能调整法宝'};
    const items=targets.map(slot=>({itemId:slot.itemId,name:slot.name || slot.itemId,slot:slot.slot}));
    const names=items.map(item=>item.name);
    // 刷新后必须有凭据可恢复；持久化失败时保持原装备。
    if (!saveLoadoutMarker({player:loadoutPlayer(),at:Date.now(),reason:'moonpalace',items,order:slotOrder(profile),hold:true,restoring:false,error:''},true))
      return {skip:'恢复记录无法保存，未调整法宝'};
    try {
      for (const item of items) {
        step('月殿换装：收起「'+item.name+'」');
        await treasureAction('withdraw',item,()=>!slotActive(item.itemId),check);
      }
      check();
      const left=hitPassiveSlots()[0]?.name || hitAudit().passive;
      if (left) throw new Error('收宝后技能位里仍有「'+left+'」');
      log('月殿换装：已收起「'+names.join('」「')+'」，打完自动祭回');
      return {changed:true,names};
    } catch (error) {
      if (state.loadout) state.loadout.hold=false;
      if (error instanceof Cancelled || owner!==state.generation) { void restoreLoadout(); throw error; }
      const restored=await restoreLoadout();
      return {skip:error.message+(restored.ok?'，已装回':'，装回未确认：'+restored.reason)};
    }
  }
  // 把收起的法宝祭回并恢复原技能位顺序。不依赖流程代次：用户按了停止也要装回；副本里装不了就留着记录，稍后再试。
  let loadoutRestoring=null;
  function restoreLoadout() {
    return loadoutRestoring ||= (async()=>{
      const marker=state.loadout;
      if (!marker) return {ok:true,changed:false};
      const fail=reason=>{ if (state.loadout===marker) saveLoadoutMarker({...marker,hold:false,restoring:false,error:reason,failedAt:Date.now()}); return {ok:false,reason}; };
      try {
        const player=loadoutPlayer();
        if (marker.player && player && marker.player!==player) { saveLoadoutMarker(null); return {ok:true,changed:false}; }
        if (instanceCombatActive()) return fail('副本进行中');
        if (!doufaProfile()) return fail('斗法档案未载入');
        marker.restoring=true;
        for (const item of marker.items) {
          if (slotActive(item.itemId)) continue;
          await treasureAction('manifest',item,()=>slotActive(item.itemId));
        }
        const same=()=>JSON.stringify(slotOrder())===JSON.stringify(marker.order);
        let order='';
        if (!same()) {
          const current=slotOrder();
          // 祭回后顺序变了才还原；法宝集合已不同（期间手动换过）就不动用户的新配置。
          if (current.length===marker.order.length && marker.order.every(id=>current.includes(id)) && typeof window.saveDoufaSkillSlots==='function') {
            for (let attempt=0; attempt<3 && !same(); attempt++) {
              await Promise.resolve().then(()=>window.saveDoufaSkillSlots(marker.order.slice())).catch(()=>{});
              for (let i=0; i<10 && !same(); i++) await sleep(300);
            }
            order=same()?'，技能位顺序已还原':'，技能位顺序未能还原，请在法宝面板核对';
          } else order='，技能位配置与收起前不同，未改动';
        }
        const names=marker.items.map(item=>item.name).join('」「');
        saveLoadoutMarker(null);
        state.loadoutLast={at:Date.now(),names:marker.items.map(item=>item.name),order:order || '，技能位顺序不变'};
        log('月殿换装：已祭回「'+names+'」'+(order || '，技能位顺序不变'));
        return {ok:true,changed:true,order};
      } catch (error) {
        const reason=error?.message || String(error);
        log('月殿换装：「'+marker.items.map(item=>item.name).join('」「')+'」尚未装回 · '+reason+'；稍后自动重试，也可在储物袋手动祭出');
        return fail(reason);
      }
    })().finally(()=>{ loadoutRestoring=null; });
  }
  // 空闲时补做没完成的装回（流程被停止、页面刷新、当时还在副本里）。
  function retryLoadoutRestore() {
    const marker=state.loadout;
    if (!marker || marker.hold || loadoutRestoring || state.busy || state.running || instanceCombatActive() || !doufaProfile()) return;
    if (marker.failedAt && Date.now()-marker.failedAt<15000) return;
    void restoreLoadout();
  }
  // 月殿这一场结束（通关、跳过、出错或被停止）后装回。退出副本需要一点时间；还在副本里就交给空闲重试。
  async function finishMoonLoadout(owner) {
    if (!state.loadout) return;
    state.loadout.hold=false;
    for (let i=0; i<20 && instanceCombatActive() && owner===state.generation; i++) await sleep(300);
    const restored=await restoreLoadout();
    if (!restored.ok && state.batch?.status==='running') state.batch.loadout='「'+(state.loadout?.items || []).map(item=>item.name).join('」「')+'」尚未装回：'+restored.reason;
    else if (state.batch) delete state.batch.loadout;
  }
  async function loadoutCheck() {
    return workflow(async owner => {
      const before=slotOrder();
      const swap=await stripHitPassives(owner);
      if (swap.skip) return '换装自检未通过：'+swap.skip;
      if (!swap.changed) return '技能位里没有放大命中数的被动法宝，无需换装';
      const clean=!hitAudit().passive && !hitPassiveSlots().length, during=slotOrder();
      if (state.loadout) state.loadout.hold=false;
      const restored=await restoreLoadout();
      checkOwner(owner);
      const after=slotOrder(), same=JSON.stringify(before)===JSON.stringify(after);
      state.loadoutCheck={at:Date.now(),names:swap.names,before,during,after,clean,restored:restored.ok,same};
      if (!restored.ok) return '换装自检未通过：收起成功，装回未确认 · '+restored.reason;
      return '换装自检'+(clean && same?'通过':'有出入')+'：收起「'+swap.names.join('」「')+'」后'+(clean?'被动已不在技能位':'被动仍在技能位')+'，已祭回，技能位顺序'+(same?'与原先一致':'与原先不同');
    }, {preserveResult:true,preserveError:true,returnOnError:false});
  }
  class SettlementError extends Error {
    constructor(arena, message, combat = readCombat()) {
      super(message); this.code = 'settlement_anomaly'; this.arena = arena;
      const receipt = combat?.arena === arena ? instanceResult(combat, arena)?.result : null;
      this.outcome = receipt?.kind || combat?.trial?.anomaly || 'settlement_anomaly';
      this.flags = Array.isArray(receipt?.flags) ? receipt.flags.slice() : [];
      this.runId = combatRunId(combat);
      this.hits = combat?.metrics?.enemyHits ?? receipt?.hits ?? null;
      // 月殿按玩家命中次数校验（count_cap:hits），保留实际上报的那个数和放大来源。
      if (arena==='moonpalace' && combat?.arena===arena) {
        const audit=hitAudit(combat);
        if (audit.playerHits!=null) { this.hits=audit.playerHits; this.swordHits=audit.swordHits; this.hitPassive=audit.passive; }
      }
    }
  }
  function combatRunId(combat) {
    return (Object.hasOwn(TRIALS, combat?.arena) ? combat?.trial?.run?.id : instanceResult(combat, combat?.arena)?.run?.id) || null;
  }
  function markSkippedSettlement(combat) {
    if (window.__fc && combat) skippedCombatSettlements.set(window.__fc, {arena:combat.arena,runId:combatRunId(combat)});
  }
  function settlementWasSkipped(combat) {
    const old = window.__fc && skippedCombatSettlements.get(window.__fc);
    return !!old && old.arena === combat?.arena && old.runId === combatRunId(combat);
  }
  async function waitInstanceSettlement(id, owner, label) {
    try {
      await until(() => {
        const live = readCombat();
        if (!live || live.arena !== id) throw new Error('结算期间副本场景已切换');
        const view = instanceResult(live, id);
        return !!view?.result || !!view?.error;
      }, CFG.settlementTimeoutMs, label, owner);
    } catch (error) {
      if (error instanceof Cancelled || owner !== state.generation) throw error;
      if (!error.message.startsWith('等待超时：')) throw error;
      throw new SettlementError(id, error.message);
    }
    checkOwner(owner);
    const final = instanceResult(readCombat(), id);
    if (final?.error) throw new SettlementError(id, '副本结算异常：' + final.error);
    if (final?.result?.kind !== 'clear' && final?.result?.kind !== 'failure')
      throw new SettlementError(id, '副本结算未确认：' + (final?.result?.kind || '未知') +
        (final?.result?.flags?.length ? ' · ' + final.result.flags.join(',') : ''));
    return final;
  }
  function combatSettlementError(combat) {
    if (!combat?.active || combat.overworld) return null;
    if (Object.hasOwn(TRIALS, combat.arena) && (combat.trial?.syncError || combat.trial?.anomaly))
      return new SettlementError(combat.arena, '战绩同步异常：' + (combat.trial.syncError || combat.trial.anomaly), combat);
    const receipt = instanceResult(combat, combat.arena);
    if (receipt?.error) return new SettlementError(combat.arena, '副本结算异常：' + receipt.error, combat);
    if (receipt?.result && !['clear', 'failure'].includes(receipt.result.kind))
      return new SettlementError(combat.arena, '副本结算未确认：' + (receipt.result.kind || '未知'), combat);
    return null;
  }
  async function skipSettlement(error, owner, {exitCombat=true} = {}) {
    checkOwner(owner);
    const combat = readCombat(), runtime = window.__fc;
    if (exitCombat && combat?.active && combat.arena !== error.arena) throw new Error('异常副本场景已切换，未退出新战斗');
    state.retry.enabled = false; stopDriving();
    const row = {id:error.arena,name:INSTANCE_LABELS[error.arena] || TRIALS[error.arena] || error.arena,
      reason:error.message,outcome:error.outcome,flags:error.flags,runId:error.runId,hits:error.hits,at:Date.now()};
    if (error.swordHits!=null) row.swordHits=error.swordHits;
    if (error.hitPassive) row.hitPassive=error.hitPassive;
    if (error.flags?.includes('count_cap:hits')) {
      row.reason+=' · 上报命中 '+error.hits+' 次超过服务端上限'+(error.hitPassive?'；'+hitAuditText({passive:error.hitPassive}):'');
    }
    state.lastSettlementSkip = row;
    if (state.result?.arena === error.arena)
      state.result = {...state.result,verified:false,code:error.code,settlementError:error.message};
    if (state.batch?.status === 'running') {
      state.batch.skipped.push({...row}); state.batch.history.push({...row});
      if (state.batch.history.length > 50) state.batch.history.shift();
    }
    if (exitCombat && combat?.arena === error.arena) markSkippedSettlement(combat);
    if (combat?.arena === error.arena) recordInstanceRun(error.arena,combat,{reason:safeLogText(error.message,160)});
    reportFailure(error);
    step('结算异常，跳过本场不重试：' + row.name + ' · ' + error.message);
    if (exitCombat && combat?.active) {
      if (combat.state === 'fighting' && !combat.paused) act('pause');
      // 只调用原生退出，不重新提交 end、不 retry、不 start，也不等待已拒绝回执。
      if (!act('stop')) throw new Error('游戏未接受退出异常副本，已停止队列并保留保护');
      await until(() => {
        if (window.__fc !== runtime) throw new Error('退出异常副本时场景已切换');
        return !readCombat()?.active;
      }, 10000, '退出结算异常副本', owner);
    }
    checkOwner(owner); state.error = '';
    if (state.story) {state.story.moonCombat=null;state.story.moonShots=null;state.story.moonAvoid=null;state.story.mirrorSeen=null;state.story.moonWheelChecked=false;}
  }
  async function instanceObjective(q, objective, owner) {
    const id = objective.instance, label = INSTANCE_LABELS[id];
    if (!label) throw new Error('未知主线副本：' + id);
    let s = readCombat();
    checkOwner(owner);
    const existingAnomaly = s?.arena === id && combatSettlementError(s);
    if (existingAnomaly) throw existingAnomaly;
    if (!s?.active || s.overworld || s.arena !== id) {
      step('主线副本：' + label);
      if (s?.arena !== id || s.overworld) await travelMenu(label, owner);
      await until(() => {
        const live = readCombat();
        if (live?.active && !live.overworld && live.arena === id && live.state === 'fighting') return true;
        return window.__rt.startTownCombat(id) === true;
      }, 420000, '剧情副本开战', owner);
      if (window.__fc) skippedCombatSettlements.delete(window.__fc);
    }
    arenaInfo = null; state.navigation=null;
    state.lastHp = s?.hp ?? null;
    if (id==='moonpalace') warnHitInflation(label);
    // 演练（drill）：明知本局不发奖励也打完，用来观察走位与受击；演练局结束即返回，不重打、不要求通关回执。
    const drill = !q && objective.drill === true;
    const drillEnd = async outcome => {
      const until0 = Date.now() + 15000;
      while (Date.now() < until0) {
        const view = instanceResult(readCombat(), id);
        if (view?.result || view?.error || readCombat()?.arena !== id) break;
        await storyWait(300, owner);
      }
      const live = readCombat(), row = recordInstanceRun(id, live, {drill:true});
      return {kind:instanceResult(live, id)?.result?.kind || outcome, drill:true, outcome, seconds:row?.seconds ?? null,
        enemyHits:live?.metrics?.enemyHits ?? null, hp:live?.hp ?? null, hpMax:live?.hpMax ?? null};
    };
    const deadline = Date.now() + 20 * 60000;
    while (true) {
      checkOwner(owner);
      const anomaly = combatSettlementError(readCombat());
      if (anomaly && !(drill && anomaly.outcome === 'practice')) throw anomaly;
      await storyReady(owner);
      s = readCombat();
      if (!s?.active || s.overworld || s.arena !== id) throw new Error('剧情副本已退出');
      recordDamage(s, performance.now());
      if (Date.now() > deadline) throw new Error('剧情副本超时');
      const result = instanceResult(s, id);
      if (result?.error) throw new SettlementError(id, '剧情副本记录异常：' + result.error, s);
      // 每日副本的开局回执标明本局不发奖励（今日奖励已领，或战败后的重打）：立即退出，不打完整场演练。
      if (!q && !drill && s.state === 'fighting' && result?.run?.rewarded === false && !result.result)
        throw new SettlementError(id, '本局为演练：今日奖励已领或已用掉，不再继续打', s);
      const practice = drill && result?.run?.rewarded === false;
      if (practice && ['defeat','victory'].includes(s.state)) { releaseKeys(); return drillEnd(s.state); }
      if (s.state === 'defeat' && !q) {
        releaseKeys();
        await waitInstanceSettlement(id, owner, '副本失败结算');
        storyCheck(owner);
        recordInstanceRun(id, readCombat());
        let quota;
        try { quota = await dailyStatus(id, owner); }
        catch (error) {
          if (error instanceof Cancelled || owner !== state.generation) throw error;
          throw new SettlementError(id, '副本失败后奖励回执异常：' + error.message);
        }
        storyCheck(owner); batchDay(quota.day);
        if (quota.claimed) throw new SettlementError(id, '副本失败后奖励状态改变，请核对结算');
        state.batch.history.push({ id, outcome:'defeat', at:Date.now() });
        if (state.batch.history.length>20) state.batch.history.shift();
        state.story.moonCombat=null; state.story.moonShots=null;state.story.moonAvoid=null; state.story.mirrorSeen=null; state.story.moonWheelChecked=false;
        step('每日副本重试：' + label);
        if (!act('start')) throw new Error('游戏未接受副本重新挑战');
        await storyWait(300, owner); continue;
      }
      if (s.state === 'victory') {
        releaseKeys();
        const final = await waitInstanceSettlement(id, owner, '剧情副本结算');
        storyCheck(owner);
        if (!q) {
          if (final?.result?.kind !== 'clear') throw new SettlementError(id, '副本结算未确认通关：' + (final?.result?.kind || '未知'));
          recordInstanceRun(id, readCombat());
          return final.result;
        }
        await window.__quests.refresh(); storyCheck(owner);
        if ((storySlot(q.id)?.progress?.[objective.id] || 0) < (storySlot(q.id)?.required?.[objective.id] ?? objective.required)) {
          throw new SettlementError(id, '副本已结束，但服务端尚未确认主线进度，请核对结算');
        }
        // 正常退出胜利面板，避免交任务时仍由副本控制人物。
        act('stop'); return;
      }
      catchVisibleSword();
      const enemies = livingEnemies(s), me = readWorld().actorPosition;
      if (id === 'moonpalace') ensureMoonWheel();
      if (id === 'moonpalace' && await moonMechanicFrame(s, owner)) { await storyWait(CFG.tickMs, owner); continue; }
      if (id === 'bloodland' && !enemies.length && s.bloodLand?.herbs?.length < 5) {
        const herb = window.qingxiBloodLand?.arena?.herbs?.filter(h => !s.bloodLand.herbs.includes(h.id))
          .sort((a, b) => dist(me, [a.x,0,a.z]) - dist(me, [b.x,0,b.z]))[0];
        if (!herb) throw new Error('禁地灵药位置未载入');
        await walkTo([herb.x,herb.y,herb.z], 1, owner, { defend: false, interrupt: () => livingEnemies(readCombat()).length > 0 });
        releaseKeys(); await storyWait(250, owner); continue;
      }
      if (id === 'escort' && !enemies.length) {
        const p = escortPoint(s.escort?.dist || 0);
        await walkTo(p, 6, owner, { defend: false, interrupt: () => livingEnemies(readCombat()).length > 0 });
        releaseKeys(); await storyWait(200, owner); continue;
      }
      if (id === 'jianzhong') {
        const floor = SWORD_FLOORS[Math.min(s.wave.index + (s.wave.interlude > 0 ? 1 : 0), 2)];
        const meter = document.querySelector('.jz-meter:not([hidden])')?.textContent || '';
        const pressure = Number(meter.match(/剑意\s*(\d+)/)?.[1] || 0);
        if (pressure > 70 || state.story.settlingSword && pressure > 20) {
          state.story.settlingSword = true;
          const right = me[0] > floor.point[0] ? 1 : -1;
          const stele = [floor.point[0] + right * (floor.radius + 3.4), floor.point[1], floor.point[2] + 2];
          await walkTo(stele, 2.5, owner, { interrupt: () => readCombat()?.state !== 'fighting' }); releaseKeys(); await storyWait(150, owner); continue;
        }
        state.story.settlingSword = false;
        if (s.wave.interlude > 0 || Math.abs(me[1] - floor.point[1]) > 3 || dist(me, floor.point) > floor.radius + 5) {
          await walkTo(floor.point, 6, owner, { defend: false, interrupt: () => readCombat()?.state !== 'fighting' });
          await storyWait(150, owner); continue;
        }
      }
      if (!enemies.length || s.wave.interlude > 0) { releaseKeys(); await storyWait(150, owner); continue; }
      const focus = id === 'southgate' ? [0,0,114] : id === 'escort' ? escortPoint(s.escort?.dist || 0) : me;
      const target = enemies.sort((a, b) => dist(a.position, focus) - dist(b.position, focus))[0];
      const a = captureArena(s, me);
      const boundary = id === 'jianzhong' ? { center: SWORD_FLOORS[s.wave.index].point, radius: SWORD_FLOORS[s.wave.index].radius + 2 }
        : { center: a.center, radius: Math.max(5, a.radius - 4) };
      storyBattleFrame(s, target, boundary);
      await storyWait(CFG.tickMs, owner);
    }
  }

  function closeStoryFishing() {
    const panel = document.getElementById('worldFishingPanel');
    const reel = panel?.querySelector('[data-fishing="reel"]');
    reel?.dispatchEvent(new KeyboardEvent('keyup', {code:'Space',key:' ',bubbles:true}));
    if (panel && !panel.hidden) panel.querySelector('[data-fishing="close"]')?.click();
  }
  function fishingInput(panel, holding) {
    const reel = panel.querySelector('[data-fishing="reel"]');
    if (!reel || reel.disabled) return;
    reel.dispatchEvent(new KeyboardEvent(holding ? 'keydown' : 'keyup', {code:'Space',key:' ',bubbles:true}));
  }
  function fishingHoldDecision(panel, wasHolding) {
    const tension = Number(panel.querySelector('#worldFishingTensionText')?.textContent);
    const zone = panel.querySelector('#worldFishingTensionZone');
    const low = parseFloat(zone?.style.left), high = low + parseFloat(zone?.style.width);
    if (![tension,low,high].every(Number.isFinite)) return false;
    const margin = Math.max(3,(high-low)*0.2);
    return tension < low+margin ? true : tension > high-margin ? false : wasHolding;
  }
  async function fishObjective(q, objective, owner) {
    if (objective.venue !== 'qingxi-town') throw new Error('尚未识别钓鱼场地：'+objective.venue);
    const site = window.resolveDwellingFishingSite?.('qingxi-riverside', myPos());
    if (!site?.position) throw new Error('河岸钓台尚未载入');
    await approach(position3(site.position), 0.6, owner);
    await waitForStoryStill(owner);
    const world = readWorld();
    const target = (world.targets || []).find(t => t.kind === 'fishing-spot' && t.id === 'fishing:'+(site.stationId || site.id));
    if (!target || !window.__rt.selectTarget(target.id)) throw new Error('钓位尚未准备好，请检查钓位占用');
    window.__rt.interact();
    await until(() => shown('worldFishingPanel'), 30000, '正常打开河岸钓台', owner);
    let holding = false, refreshedAt = 0, stalledAt = Date.now(), previous = '';
    const end = Date.now()+8*60000;
    try {
      while (Date.now() < end) {
        storyCheck(owner); releaseKeys();
        const panel = document.getElementById('worldFishingPanel');
        if (!panel || panel.hidden) throw new Error('钓鱼面板已关闭');
        if (document.hidden || readCombat()?.paused) { fishingInput(panel,false); holding=false; await storyWait(200,owner); continue; }
        const phase = panel.dataset.phase;
        const progress = storySlot(q.id)?.progress?.[objective.id] || 0;
        if (progress >= objective.required) return;
        const stamp = phase+':'+progress;
        if (stamp !== previous) { previous=stamp; stalledAt=Date.now(); }
        const click = name => {const b=panel.querySelector('[data-fishing="'+name+'"]');if(b&&!b.disabled)b.click();};
        if (phase === 'bite') click('hook');
        else if (phase === 'caught') click('land');
        else if (['ready','result','miss'].includes(phase)) {
          if (phase === 'result') {
            await window.__quests.refresh(false); storyCheck(owner);
            if ((storySlot(q.id)?.progress?.[objective.id] || 0) >= objective.required) return;
          }
          const quota = panel.querySelector('#worldFishingQuota')?.textContent?.match(/今日剩余\s*(\d+)\s*\//);
          if (quota && Number(quota[1])===0 && panel.querySelector('[data-fishing="cast"]')?.disabled) {
            const error = new Error('今日竿数已用完，明日再来。');
            error.code='fishing_daily_limit_reached'; throw error;
          }
          click('cast');
          if (Date.now()-stalledAt>20000) throw new Error(panel.querySelector('#worldFishingStatus')?.textContent || '渔具、鱼饵或今日钓鱼次数不可用');
        } else if (phase === 'fighting') {holding=fishingHoldDecision(panel,holding);fishingInput(panel,holding);}
        else {fishingInput(panel,false);holding=false;}
        if (Date.now()-refreshedAt>2000 && ['result','miss'].includes(phase)) {
          await window.__quests.refresh(false); storyCheck(owner); refreshedAt=Date.now();
        }
        await storyWait(80,owner);
      }
      throw new Error('钓鱼任务超时，请检查这一竿的真实结算状态');
    } finally { closeStoryFishing(); }
  }

  // 路线只驱动实际移动；检查点和三维穿环均由游戏原生 bridge 验证、上报。
  async function pursuitObjective(q, objective, owner) {
    const points=storyData()?.yufeng?.routes?.[q.id];
    if(!Array.isArray(points) || points.length!==objective.required || !window.qingxiYufengBridge) throw new Error('追逐路线尚未载入，请重新进入黄土荒野');
    const deadline=Date.now()+6*60000;
    let last=Number(storySlot(q.id)?.progress?.[objective.id] || 0), resets=0;
    while(last<objective.required) {
      await storyReady(owner);
      if(Date.now()>deadline)throw new Error('白虹追至多次未完成，已停止重试');
      const index=Number(storySlot(q.id)?.progress?.[objective.id] || 0);
      if(index<last && ++resets>2)throw new Error('白虹追至连续超时，请检查路线');
      last=index;if(index>=objective.required)return;
      const point=position3(points[index]);
      if(!point)throw new Error('追逐光环位置无效');
      const y=fieldEnvironment.groundHeight(point[0],point[2],myPos()[1]);
      if(!Number.isFinite(y))throw new Error('追逐路线地形未就绪');
      point[1]=y;
      step('白虹追至 · 引路光环 '+(index+1)+'/'+points.length);
      const changed=()=>Number(storySlot(q.id)?.progress?.[objective.id] || 0)!==index;
      await walkTo(point,5.5,owner,{flight:false,interrupt:changed});
      const end=Date.now()+10000;
      while(!changed()) {
        await storyReady(owner);releaseKeys();
        if(Date.now()>end)throw new Error('第 '+(index+1)+' 处追逐光环未获游戏确认');
        await storyWait(100,owner);
      }
    }
  }
  function yufengCourse() {
    const course=window.YufengRaceCourse, required=storySlot('yfc_q08')?.required?.route || storyQuest('yfc_q08')?.objectives?.find(o=>o.id==='route')?.required;
    if(!course || !Array.isArray(course.gates) || course.gates.length!==required || !Array.isArray(course.dock) || !Number.isFinite(course.limit)) throw new Error('御风车赛道与任务目录不一致，请刷新游戏后重试');
    if(course.gates.some(g=>!position3(g.position) || g.position.length!==3 || !position3(g.normal) || g.normal.length!==3 || !(g.radius>4))) throw new Error('御风车三维风环资料无效');
    return course;
  }
  const spaceDistance=(a,b)=>Math.hypot(...a.map((v,i)=>v-b[i]));
  function gateOffset(gate, distance) { return gate.position.map((v,i)=>v+gate.normal[i]*distance); }
  function gateSide(p, gate) { return p.reduce((sum,v,i)=>sum+(v-gate.position[i])*gate.normal[i],0); }
  function nativeRaceGate(total) {
    const server=Number(storyData()?.yufeng?.race?.gate || 0);
    // 原生队列可能稍晚收到回执；用它的本地穿环序号继续飞，最终仍以服务端完赛为准。
    try {
      const local=JSON.parse(document.getElementById('worldYufengChariot')?.dataset.route || '{}');
      if(local.qid==='yfc_q08' && Number.isInteger(local.index) && local.index>=server && local.index<=total)return local.index;
    } catch { /* 本地 HUD 未载入时等服务端确认。 */ }
    return server;
  }
  function chooseRaceInput(world, target, course) {
    const p=world.actorPosition, speed=(world.flight.cruiseSpeed || 25)*Math.max(.15,world.flight.race?.speed ?? 1);
    const pitch=Math.max(-1.25,Math.min(1.25,(world.pitch || 0)-(state.story.flightRequest?.pitch || 0)));
    const basis=movementBasis(world), distance=spaceDistance(p,target), seconds=Math.max(.06,Math.min(.18,distance/speed*.65));
    let best=null;
    // 竞速松键仍会自动向前；候选只使用真实的八方向输入，按原生规则单位化。
    for(const keys of KEY_SETS) {
      let forward=Number(keys.includes('w'))-Number(keys.includes('s')),right=Number(keys.includes('d'))-Number(keys.includes('a'));
      const length=Math.max(1,Math.hypot(forward,right));forward/=length;right/=length;
      const dx=-Math.sin(basis)*forward*Math.cos(pitch)+Math.cos(basis)*right;
      const dz=-Math.cos(basis)*forward*Math.cos(pitch)-Math.sin(basis)*right;
      const desired=(target[1]-p[1])/Math.max(.1,dist(p,target))*Math.hypot(dx,dz);
      const vertical=Math.max(-1,Math.min(1,(desired-Math.sin(pitch)*forward)/.4));
      for(const up of [vertical,-1,0,1]) {
        const dy=Math.sin(pitch)*forward+up*.4, scale=Math.hypot(dx,dy,dz);
        const next=[p[0]+dx/scale*speed*seconds,p[1]+dy/scale*speed*seconds,p[2]+dz/scale*speed*seconds];
        if(!flightSegment(p,next,3.6))continue;
        const hazard=(course.obstacles || []).some(orb=>window.YufengRace?.orbContact?.(p,next,orb,3));
        const score=spaceDistance(next,target)+(hazard?50:0);
        if(!best || score<best.score)best={keys,vertical:up,score,hazard};
      }
    }
    return best;
  }
  async function returnYufengDock(course, owner) {
    storyCheck(owner);releaseKeys();window.__rt.setFlightVerticalInput?.(0);
    let button;
    await until(()=>button=uiButton('.yufeng-race-hud button',text=>text==='归航停靠' || text==='返回起点'),10000,'御风车归航入口',owner);
    storyCheck(owner);
    button.click();
    await until(()=>!readWorld()?.flight?.active && dist(myPos(),position3(course.dock))<15,30000,'御风车归航停靠',owner);
    storyCheck(owner);
    if(readWorld()?.flight?.chariotMounted) {
      await window.qingxiYufengBridge.toggle();storyCheck(owner);
    }
    await until(()=>!readWorld()?.flight?.chariotMounted && !storyData()?.yufeng?.mounted,15000,'御风车落地收纳',owner);
    state.story.flying=false;
  }
  async function flyYufengRace(course, owner) {
    const end=Date.now()+(course.limit+20)*1000;
    let index=-1,stage='approach',startedAt=Date.now(),crossedAt=0,retries=0,blockedAt=0;
    while(true) {
      if(document.hidden)throw new Error('页面转入后台，御风车试驾已停止，请回到页面后重新运行');
      await storyReady(owner);
      const race=storyData()?.yufeng?.race,world=readWorld();
      if(race?.status==='finished')return;
      if(race?.status!=='running' || !world.flight?.active || ['landing','dive'].includes(world.flight.phase))throw new Error('御风车试驾已中断，进度未记为完成');
      if(Date.now()>end)throw new Error('御风车试驾超过时限，进度未记为完成');
      if(world.flight.phase!=='cruise'){await storyWait(60,owner);continue;}
      const next=nativeRaceGate(course.gates.length),p=world.actorPosition;
      if(next!==index){index=next;stage='approach';startedAt=Date.now();crossedAt=0;retries=0;step('御风初行 · 风环 '+Math.min(index+1,course.gates.length)+'/'+course.gates.length);}
      if(Date.now()-startedAt>25000)throw new Error('第 '+(index+1)+' 道风环长期未确认');
      let target;
      if(index>=course.gates.length) {
        // 等最后的原生上报队列确认时在终环外盘旋，松键并不能停住飞车。
        const center=gateOffset(course.gates.at(-1),32),angle=(Date.now()-startedAt)/800;
        target=[center[0]+Math.cos(angle)*10,center[1]+3,center[2]+Math.sin(angle)*10];
      } else {
        const gate=course.gates[index],before=gateOffset(gate,-22);
        if(stage==='approach' && spaceDistance(p,before)<5 && gateSide(p,gate)<-8)stage='cross';
        target=stage==='cross'?gateOffset(gate,22):before;
        if(stage==='cross' && gateSide(p,gate)>8) {
          crossedAt ||= Date.now();
          if(Date.now()-crossedAt>1500) {
            if(++retries>3)throw new Error('第 '+(index+1)+' 道风环未获确认，停止反复补环');
            stage='approach';crossedAt=0;target=before;
          }
        }
      }
      const input=chooseRaceInput(world,target,course);
      if(input) {
        blockedAt=0;setKeys(input.keys);window.__rt.setFlightVerticalInput(input.vertical);
        if(input.hazard && world.flight.race?.energy>=1 && !world.flight.race?.surge && !world.flight.race?.stun)window.__rt.boostYufengRace?.();
      } else {
        blockedAt ||= Date.now();
        if(Date.now()-blockedAt>2000)throw new Error('御风车前方地形无可行飞行方向');
        window.__rt.setFlightVerticalInput(1);
      }
      state.navigation={mode:'race',position:p.slice(),goal:target,waypoint:target,distance:spaceDistance(p,target),keys:input?.keys.join('') || '',reason:'原生确认 '+(race.gate || 0)+'/'+course.gates.length,updatedAt:Date.now()};
      await storyWait(60,owner);
    }
  }
  async function raceObjective(q, objective, owner) {
    const course=yufengCourse(),bridge=window.qingxiYufengBridge;
    if(!bridge?.toggle)throw new Error('御风车原生控制器尚未载入');
    if((storySlot(q.id)?.progress?.[objective.id] || 0)>=objective.required) {
      if(readWorld()?.flight?.active || readWorld()?.flight?.chariotMounted)await returnYufengDock(course,owner);
      return;
    }
    if(storyData()?.yufeng?.race?.status!=='running' || !readWorld()?.flight?.active) {
      if(readWorld()?.flight?.active)await landStoryFlight(owner);
      if(readWorld()?.flight?.chariotMounted){await bridge.toggle();storyCheck(owner);}
      await approach(position3(course.dock),3,owner);
      state.story.flightRequest={pitch:readWorld()?.pitch || 0,cancelled:false};state.story.flying=true;
      await bridge.toggle({race:true});storyCheck(owner);
      await until(()=>storyData()?.yufeng?.race?.status==='running' && readWorld()?.flight?.active,15000,'原生登车竞速',owner);
    } else {
      // 接管已经起飞的试驾时，镜头起始俯仰不可重建，回起点正常重开。
      await returnYufengDock(course,owner);
      return raceObjective(q,objective,owner);
    }
    try {
      await flyYufengRace(course,owner);
      await returnYufengDock(course,owner);
      await window.__quests.refresh(false);storyCheck(owner);
      if(storyData()?.yufeng?.race?.status!=='finished' || (storySlot(q.id)?.progress?.[objective.id] || 0)<objective.required)throw new Error('试驾完赛尚未获得服务端确认');
    } finally {
      if(owner===state.generation){releaseKeys();cancelStoryFlight(state.story);}
    }
  }
  async function routeObjective(q, objective, owner) {
    if(q.id==='yfc_q05' && objective.worldObject==='yfc_chase')return pursuitObjective(q,objective,owner);
    if(q.id==='yfc_q08' && objective.worldObject==='yfc_pilot')return raceObjective(q,objective,owner);
    throw new Error('尚未支持的限时路线：'+q.title);
  }

  async function executeStoryStep(row, owner) {
    const q = storyQuest(row.id);
    const unsupported = row.objectives.map(o=>unsupportedObjective(q,o)).filter(Boolean);
    if (row.status !== 'ready' && unsupported.length) {
      const error = new Error(row.title+' · '+unsupported.join('、')); error.code='unsupported_quest_objective'; throw error;
    }
    if (row.status === 'available' || row.status === 'ready') {
      const npcId = row.status === 'available' ? q.giver : q.receiver;
      await approachNpc(npcId, owner);
      const action = row.status === 'available' ? 'accept' : 'turn-in';
      const body = { questId: q.id, npcId };
      if (action === 'turn-in' && q.rewards?.choices?.length) {
        const wanted = state.story.options.rewardChoices?.[q.id];
        const choice = wanted ? q.rewards.choices.find(c => c.id === wanted) : q.rewards.choices[0];
        if (!choice) throw new Error('奖励选项不存在：' + wanted);
        body.choice = choice.id; log('主线奖励选择：' + choice.label);
      }
      await storyRequest(action, body, owner); return;
    }
    const objective = row.objectives.find(o => o.progress < o.required);
    if (!objective) { await window.__quests.refresh(); storyCheck(owner); return; }
    if (objective.type === 'talk') {
      await approachNpc(objective.npc, owner);
      await storyRequest('talk', { npcId: objective.npc }, owner);
    } else if (objective.type === 'instance') await instanceObjective(q, objective, owner);
    else if (objective.type === 'puzzle') await puzzleStep(q, objective, owner);
    else if (objective.type === 'catch') await fishObjective(q, objective, owner);
    else if (objective.type === 'route') await routeObjective(q, objective, owner);
    else if (objective.arena && (objective.enemyRole || objective.sourceEnemyRole)) await fieldObjective(q, objective, owner);
    else if (['collect','gather','interact'].includes(objective.type)) {
      if(q.id==='yfc_q08' && objective.worldObject==='yfc_dock' && (readWorld()?.flight?.active || readWorld()?.flight?.chariotMounted || dist(myPos(),position3(yufengCourse().dock))>15)) await returnYufengDock(yufengCourse(),owner);
      const nodes = (storyData()?.nodes || []).filter(n => (n.itemId || n.worldObject || n.id) === (objective.itemId || objective.worldObject));
      const node = nodes.sort((a, b) => dist(myPos(), position3(a.position)) - dist(myPos(), position3(b.position)))[0];
      if (!node) throw new Error('任务采集物尚未出现：' + objective.text);
      await gatherNode(node, owner);
    } else throw new Error('未识别的任务目标类型：' + objective.type);
  }
  async function runStory(options = {}) {
    const kind = options.kind ?? '主线';
    if (typeof kind !== 'string' || !kind) return '任务类型无效';
    state.questKind = kind;
    state.panelView='quests';
    const maxChapter = options.maxChapter ?? Infinity, maxMinutes = options.maxMinutes ?? 180;
    const maxTasks = options.maxTasks ?? Infinity;
    if (options.maxTasks != null && (!Number.isInteger(maxTasks) || maxTasks < 1)) return 'maxTasks 必须为正整数';
    if (options.maxChapter != null && (!Number.isInteger(maxChapter) || maxChapter < 1) || !Number.isFinite(maxMinutes) || maxMinutes <= 0) return '主线参数无效：maxChapter 必须为正整数，maxMinutes 必须大于 0';
    return workflow(async owner => {
      await prepareTown(owner);
      await until(() => !!window.__quests, 30000, '主线控制器', owner);
      await loadQuestCatalog(); checkOwner(owner);
      if (!['全部任务','全部支线',...taskKinds()].includes(kind)) throw new Error('任务类型无效：'+kind);
      if(readWorld()?.flight?.chariotMounted && storyData()?.yufeng?.race?.status==='running') throw new Error('当前御风车试驾正在进行，保留本次飞行；请先完赛或在游戏内返回起点后启动任务');
      if (!fieldEnvironment) throw new Error('未捕获道路碰撞数据，请启用脚本后重新进入坊市');
      const combat = readCombat();
      if (combat?.active && !combat.overworld && combat.state === 'fighting' &&
        !questRows(false, kind).some(q => q.status === 'active' && q.objectives.some(o => o.instance === combat.arena && o.progress < o.required))) {
        throw new Error('请先结束当前试炼，再启动全自动主线');
      }
      if (combat?.active && !combat.overworld && combat.state === 'victory' &&
        !questRows(false, kind).some(q => q.status === 'active' && q.objectives.some(o => o.instance === combat.arena && o.progress < o.required))) {
        if (combat.trial?.syncing) throw new Error('上一场试炼战绩还在同步');
        act('stop');
      }
      const rt = window.__rt;
      state.story = { runtime: rt, quests: window.__quests, kind, options, fieldReceiptStart:fieldSettlementSequence, deadline: Date.now() + maxMinutes * 60000, current: null, completed: 0, deferred: [] };
      state.questSummary = {kind,completed:0,deferred:state.story.deferred};
      const camera = rt.cameraFollowSettings?.()?.enabled;
      state.story.camera = camera;
      rt.setCameraFollowEnabled?.(false);
      try {
        window.__quests.close?.(); clickOnce('worldSheetClose'); canvas()?.focus?.({ preventScroll: true });
        await window.__quests.refresh(); storyCheck(owner);
        let stalledAt = Date.now(), lastProgress = '';
        while (true) {
          checkOwner(owner);
          const anomaly = combatSettlementError(readCombat()), affected = nextStoryQuest(maxChapter);
          if (anomaly && affected?.objectives.some(o => o.instance === anomaly.arena && o.progress < o.required)) {
            await skipSettlement(anomaly, owner);
            state.story.deferred.push({id:affected.id,title:affected.title,reason:anomaly.message});
            continue;
          }
          await storyReady(owner);
          if (state.story.completed >= maxTasks) {
            await finishInCave(owner); step(kind + '本次已完成 ' + state.story.completed + ' 项；已返回洞府'); return kind+'本次任务已完成';
          }
          const row = nextStoryQuest(maxChapter);
          if (!row) {
            const unfinished = questRows(true).filter(q => q.chapter <= maxChapter && q.status !== 'rewarded');
            if (kind !== '主线' || state.story.deferred.length) {
              await finishInCave(owner);
              const locked = unfinished.filter(q=>q.status==='locked').length, deferred=state.story.deferred;
              const deferredText=deferred.length ? '；暂缓 '+deferred.length+' 项：'+deferred.map(q=>q.title+'（'+q.reason+'）').join('、') : '';
              step(kind+'已完成 ' + state.story.completed + ' 项' + (locked ? '，另有 ' + locked + ' 项需先完成前置任务' : '') + deferredText + '；已返回洞府');
              return deferred.length ? '本次可推进任务已完成，暂缓 '+deferred.length+' 项' : '当前已解锁'+kind+'已完成';
            }
            if (unfinished.length) throw new Error('主线暂无可推进任务，仍有未完成或需手动处理任务：' + unfinished.slice(0, 3).map(q => q.title).join('、'));
            await finishInCave(owner);
            step((Number.isFinite(maxChapter) ? '主线已全部完成（至第' + maxChapter + '章）' : '主线已全部完成')+'；已返回洞府'); return '主线已完成';
          }
          state.story.current = row.id;
          // 实际击杀会推进刷新代次；未掉出任务物品时允许继续等刷新重试。
          const generations = (storyData()?.field?.enemies || []).filter(e => row.objectives.some(o =>
            o.progress < o.required && o.arena === e.arena && (o.enemyRole || o.sourceEnemyRole) === e.role))
            .map(e => [e.id, e.generation]);
          const fingerprint = row.id + ':' + storyProgress(row.id) + ':' + JSON.stringify(generations);
          if (fingerprint !== lastProgress) { lastProgress = fingerprint; stalledAt = Date.now(); }
          else if (Date.now() - stalledAt > 5 * 60000) throw new Error('主线进度持续未变化：' + row.title);
          step('全自动' + kind + ' · ' + row.title + ' · ' + STATUS_LABELS[row.status]);
          const before = storyProgress(row.id);
          try { await executeStoryStep(row, owner); }
          catch (error) {
            checkOwner(owner);
            if (error instanceof SettlementError) {
              await skipSettlement(error, owner);
              storyCheck(owner);
              state.story.deferred.push({id:row.id,title:row.title,reason:error.message});
              continue;
            }
            storyCheck(owner);
            if (error.code!=='unsupported_quest_objective' && (kind==='主线' || error.code!=='fishing_daily_limit_reached')) throw error;
            reportFailure(error);
            state.story.deferred.push({id:row.id,title:row.title,reason:error.message});
            step('暂缓任务：'+row.title+' · '+error.message);
            continue;
          }
          storyCheck(owner);
          if (row.status === 'available' || row.status === 'ready') {
            await until(() => storyProgress(row.id) !== before, 10000, '接取/交付任务进度确认', owner);
          }
          if (row.status !== 'ready' || storySlot(row.id)?.status !== 'rewarded') { await storyWait(450, owner); }
          else { state.story.completed++; state.questSummary.completed=state.story.completed; }
          await window.__quests.refresh(false); storyCheck(owner);
        }
      } catch (error) {
        if (owner === state.generation) {
          reportFailure(error);
          closeStoryFishing(); releaseKeys(); cancelStoryFlight(state.story);
          const combat = readCombat();
          if (combat?.active && combat.state === 'fighting' && !combat.paused) act('pause');
          // 公共 workflow 在 finally 清理后统一回洞府，不吞掉原始任务异常。
        }
        throw error;
      } finally {
        if (owner === state.generation) {
          closeStoryFishing(); releaseKeys(); cancelStoryFlight(state.story);
          if (typeof camera === 'boolean' && window.__rt === rt) rt.setCameraFollowEnabled?.(camera);
          state.story = null;
        }
      }
    });
  }

  // 按服务端日奖励状态推进；只调用页面原有的 status，开战/结算仍由游戏执行。
  const DAILY = [
    {id:'moonpalace',name:'月殿血誓',key:'moon',global:'worldMoonRequest'},
    {id:'bloodland',name:'血色禁地',key:'blood',global:'worldBloodRequest'},
    {id:'southgate',name:'南门守城',key:'siege',global:'worldSiegeRequest'},
    {id:'escort',name:'护民出关',key:'escort',global:'worldEscortRequest'},
    {id:'mulanshrine',name:'血祭神坛',key:'shrine',resource:'mulan-shrine'},
    {id:'bqpuppet',name:'试傀场',key:'puppet',resource:'baiqiao-puppet'},
    {id:'jianzhong',name:'剑冢',key:'jianzhong',resource:'jianzhong-trial'},
  ];
  async function rewardStatus(row, owner) {
    checkOwner(owner);
    const driver = (window.qingxiCombatContent || []).flatMap(pack => Object.entries(pack.arenas || {})).find(([id]) => id === row.id)?.[1]?.driver;
    // 内容包按地区延迟加载；在进副本前查询奖励时应使用页面已有的通用原生入口。
    const request = rewardRequests[row.key] || window[row.global] ||
      (row.resource && typeof window.qingxiInstanceRequest === 'function'
        ? (action, body) => window.qingxiInstanceRequest(row.resource, action, body)
        : driver?.request?.bind(driver));
    if (typeof request !== 'function') throw new Error('未找到奖励查询入口：' + row.name);
    let timer;
    try {
      const data = await Promise.race([Promise.resolve().then(() => request('status',row.arena?{arena:row.arena}:{})), new Promise((_,reject) => {
        timer = setTimeout(() => reject(new Error('奖励查询超时：' + row.name)), 30000);
      })]);
      checkOwner(owner);
      if (data?.error || data?.ok === false) throw new Error('奖励查询失败：' + (data.error || row.name));
      const responseKey = REWARD_RESOURCES[row.resource]?.key;
      const payload = responseKey && data?.[responseKey] || data;
      if (payload?.error || payload?.ok === false) throw new Error('奖励查询失败：' + (payload.error || row.name));
      const status = payload?.[row.key === 'trial' ? 'status' : row.key];
      if (!status || !/^\d{4}-\d{2}-\d{2}$/.test(status.day)) throw new Error('奖励状态不完整：' + row.name);
      return status;
    } finally { clearTimeout(timer); }
  }
  async function dailyStatus(id, owner) {
    const row = DAILY.find(row => row.id === id);
    if (!row) throw new Error('未知每日副本：' + id);
    const status = await rewardStatus(row, owner);
    if (typeof status.claimed !== 'boolean' || typeof status.hasKey !== 'boolean') throw new Error('每日副本领奖状态不完整：' + row.name);
    return status;
  }
  function dailyEntrySkip(row, status) {
    if (status.claimed || !status.hasKey) return {reason:status.claimed?'已领奖':'未解锁'};
    // 月殿首局战败后再打为无奖演练，failClaimed 是服务端已使用当天机会的标记。
    if (row.id==='moonpalace' && status.failClaimed===true)
      return {code:'daily_exhausted',reason:'今日有奖机会已用，未领奖；不重复挑战，可手动选择演练'};
    return null;
  }
  async function inspectDailyRewards(owner, expectedDay = null) {
    const report = {status:'checking',day:expectedDay,checkedAt:null,rows:[],claimed:0,pending:0,locked:0,errors:0,verified:false};
    state.dailyRewards = report;
    try {
      for (const row of DAILY) {
        checkOwner(owner);
        step('检查每日副本领奖：' + row.name);
        let status;
        try { status = await dailyStatus(row.id, owner); }
        catch (error) {
          if (error instanceof Cancelled || owner !== state.generation) throw error;
          report.rows.push({id:row.id,name:row.name,status:'error',error:error.message}); report.errors++;
          log(row.name + '：领奖状态查询异常 · ' + error.message); continue;
        }
        checkOwner(owner);
        report.day ||= status.day;
        if (report.day !== status.day) throw new Error('日期已变化，请重新检查每日副本领奖');
        const kind = status.claimed ? 'claimed' : status.hasKey ? 'pending' : 'locked';
        report[kind]++;
        const exhausted=kind==='pending' && row.id==='moonpalace' && status.failClaimed===true;
        report.rows.push({id:row.id,name:row.name,day:status.day,hasKey:status.hasKey,claimed:status.claimed,status:kind,exhausted});
        log(row.name + '：' + (exhausted?'未领奖（今日有奖机会已用）':({claimed:'已领奖',pending:'未领奖',locked:'未解锁'})[kind]));
      }
      report.status = report.errors ? 'partial' : 'complete';
      report.verified = report.errors === 0;
      report.allClaimed = report.verified && report.pending === 0;
      return report;
    } catch (error) {
      report.status = error instanceof Cancelled || owner !== state.generation ? 'cancelled' : 'error';
      report.error = error.message; throw error;
    } finally { report.checkedAt = Date.now(); }
  }
  function dailyRewardSummary(report) {
    return '每日副本领奖检查：已领奖 ' + report.claimed + ' 场，未领奖 ' + report.pending + ' 场，未解锁 ' + report.locked + ' 场' +
      (report.errors ? '，查询异常 ' + report.errors + ' 场（未确认）' : '');
  }
  async function checkDailyRewards() {
    // 纯 status 查询：不传送、不挑战、不发送领奖请求，也不在查询异常时触发回城。
    return workflow(async owner => {
      const report = await inspectDailyRewards(owner);
      const summary = dailyRewardSummary(report); step(summary); return summary;
    }, {preserveResult:true,preserveError:true,returnOnError:false});
  }
  async function trialStatus(owner, arena = 'longwind', allowUnknown = false) {
    const status = await rewardStatus({id:'trial',key:'trial',name:TRIALS[arena],global:'worldTrialRequest',arena}, owner);
    if (status.arena && status.arena!==arena) throw new Error('试炼查询返回另一场地');
    if (![status.remaining,status.used,status.limit].every(n => Number.isInteger(n) && n >= 0) || status.remaining + status.used !== status.limit) {
      if (allowUnknown) return {...status,remaining:null,used:null,limit:null,unknown:true};
      throw new Error('试炼有奖次数不完整');
    }
    return status;
  }
  async function trialEntryQuota(owner, arena) {
    try { return await trialStatus(owner,arena,true); }
    catch (error) {
      if (error instanceof Cancelled || owner!==state.generation || error.message==='试炼查询返回另一场地') throw error;
      checkOwner(owner);
      log(TRIALS[arena]+'次数无法校验，将入场查看本局提示：'+error.message);
      const cached=readCombat()?.trial?.status;
      return {arena,day:null,remaining:null,used:null,limit:null,unknown:true,error:error.message,activeRun:cached?.activeRun || null};
    }
  }
  function updateBatchTrialQuota(arena, quota) {
    state.batch.quotas ||= {};
    state.batch.quotas[arena]=quota.unknown?null:quota.remaining;
    const values=Object.values(state.batch.quotas);
    state.batch.remaining=values.some(n=>n==null)?null:values.reduce((sum,n)=>sum+n,0);
  }
  function trialPracticeSummary(error) {
    state.batch.skipped.push({id:error.arena,name:TRIALS[error.arena],reason:error.message,outcome:'practice',code:error.code,runId:state.result?.runId || null});
    state.batch.noRewards=true;
    return '游戏确认本局无奖励，已退出并停止本轮试炼；本次有奖通关 '+state.batch.completed.length+' 场';
  }
  function batchDay(day) {
    if (!state.batch.day) state.batch.day = day;
    if (state.batch.day !== day) throw new Error('日期已变化，请重新启动一键流程');
  }
  async function batchWorkflow(mode, work) {
    return workflow(async owner => {
      state.retry.enabled = false;
      state.batch = {mode,day:null,status:'running',current:null,completed:[],skipped:[],history:[]};
      let rt, camera;
      try {
        const previous = readCombat();
        if (previous?.active && !previous.overworld) {
          if (previous.state === 'fighting' && !(mode==='daily' && DAILY.some(r=>r.id===previous.arena)) && !(mode==='trials' && Object.hasOwn(TRIALS,previous.arena))) throw new Error('请先结束当前战斗，再启动一键流程');
          if ((previous.trial?.syncing || instanceResult(previous,previous.arena)?.settling) &&
            !(mode==='daily' && DAILY.some(r=>r.id===previous.arena)) && !(mode==='trials' && Object.hasOwn(TRIALS,previous.arena)))
            throw new Error('上一场战绩仍在结算');
          if (mode==='trials' && Object.hasOwn(TRIALS,previous.arena) && previous.paused && previous.state==='fighting' && !combatSettlementError(previous)) act('pause');
          if (!['fighting','victory','defeat'].includes(previous.state)) act('stop');
        }
        await prepareTown(owner);
        // 上一轮没装回的法宝（被停止、当时还在副本里）先装回，再开始这一轮。
        if (state.loadout && !state.loadout.hold && !instanceCombatActive()) { await restoreLoadout(); checkOwner(owner); }
        await until(() => !!window.__quests,30000,'任务控制器',owner);
        if (!fieldEnvironment) throw new Error('未捕获道路碰撞数据，请启用脚本后重新进入坊市');
        rt = window.__rt; camera = rt.cameraFollowSettings?.()?.enabled;
        state.story = {runtime:rt,quests:window.__quests,options:{},deadline:Date.now()+180*60000,current:null,completed:0,camera};
        rt.setCameraFollowEnabled?.(false);
        window.__quests.close?.(); clickOnce('worldSheetClose'); canvas()?.focus?.({preventScroll:true});
        const result = await work(owner);
        await finishInCave(owner);
        checkOwner(owner); state.batch.status='complete'; state.batch.current=null;
        step(result+'；已返回洞府'); return result;
      } catch (error) {
        if (owner === state.generation) {
          reportFailure(error);
          state.batch.status='error'; state.batch.error=error.message;
          const s=readCombat();
          if (s?.active && s.state==='fighting' && !s.paused) act('pause');
        }
        throw error;
      } finally {
        if (owner === state.generation) {
          state.retry.enabled=false; stopDriving(); releaseKeys(); cancelStoryFlight(state.story);
          if (typeof camera === 'boolean' && window.__rt===rt) rt.setCameraFollowEnabled?.(camera);
          state.story=null;
        }
      }
    });
  }
  function closeWorldControls() {
    // setOpen(false) 是单向收起；不发送 Esc，避免关闭一个面板后又打开菜单。
    if (typeof window.setWorldToolsOpen==='function') { window.setWorldToolsOpen(false); return; }
    if (shown('worldSystemMenu')) {
      const resume=document.querySelector('#worldSystemMenu [data-world-resume]');
      if (resume && !resume.disabled) { resume.click(); return; }
    }
    // 兼容旧工具栏；只有明确处于展开态才切换，绝不把隐藏按钮当关闭。
    if (document.getElementById('worldToolsToggle')?.getAttribute?.('aria-expanded')==='true') clickOnce('worldToolsToggle');
  }
  function openWorldControls() {
    if (shown('worldTownEntry')) return true;
    if (typeof window.setWorldToolsOpen==='function') window.setWorldToolsOpen(true);
    else clickOnce('worldToolsToggle');
    return shown('worldTownEntry');
  }
  async function prepareBatchPortal(owner, force=false) {
    storyCheck(owner);
    const world=readWorld(), p=myPos();
    const portal=(world?.targets || []).filter(t=>t.kind==='region-portal').sort((a,b)=>dist(p,a.point)-dist(p,b.point))[0];
    if (!force && !world?.flight?.active && portal && dist(p,portal.point)<=250) return;
    const combat=readCombat();
    if (combat?.active && !combat.overworld) throw new Error('请先结算当前试炼再返回坊市');
    const story=state.story;
    step('经洞府返回青溪坊法阵'); releaseKeys(); cancelStoryFlight(story);
    // 使用游戏原有返回入口重建坊市，避开从千米外副本跨河/城墙的回城路线。
    await until(()=>{
      if (readWorld()?.environment?.tier!==4) return true;
      if (!openWorldControls()) return false;
      return clickOnce('worldTownEntry');
    },30000,'返回洞府入口',owner);
    await until(()=>{loadingFailure();return readWorld()?.environment?.tier!==4 && sceneReady();},420000,'返回洞府场景',owner);
    // 实体山门由 prepareTown 寻路交互；不要在洞府落地后再次打开系统菜单。
    await prepareTown(owner);
    await until(()=>!!window.__quests,30000,'坊市任务控制器',owner);
    checkOwner(owner);
    story.runtime=window.__rt; story.quests=window.__quests; story.flying=false; story.flightRequest=null;
    window.__rt.setCameraFollowEnabled?.(false); window.__quests.close?.(); clickOnce('worldSheetClose');
    canvas()?.focus?.({preventScroll:true}); state.navigation=null;
  }
  function townIsSafe() {
    const world=readWorld(), p=myPos(), combat=readCombat();
    return world?.environment?.tier===4 && dist(p,[0,0,0])<300 && !window.__fc?.isGhost && !world.flight?.active && !combat?.active &&
      (world.targets || []).some(t=>t.kind==='region-portal' && dist(p,t.point)<=100);
  }
  function caveIsSafe() {
    const world = readWorld(), tier = world?.environment?.tier;
    return Number.isFinite(tier) && tier !== 4 && sceneReady() && !readCombat()?.active &&
      !window.__fc?.isGhost && !world.flight?.active && !world.flight?.chariotMounted;
  }
  function caveSettlementPending(combat) {
    if (!combat || settlementWasSkipped(combat) || combat.trial?.syncError || combat.trial?.anomaly) return false;
    if ((combat.trial?.syncing || 0) > 0) return true;
    const instance = instanceResult(combat, combat.arena);
    if (instance?.settling || instance?.ending) return true;
    // 原生副本的胜/负回执可能在面板出现后才到；先等回执，不能用 stop 覆盖通关。
    return !!instance?.run && !instance.result && !instance.error &&
      (instance.run.ended || ['victory', 'defeat'].includes(combat.state));
  }
  async function finishInCave(owner) {
    checkOwner(owner); releaseKeys();
    state.home = { owner, destination:'cave', verified:false, at:Date.now(), error:'' };
    try {
      if (!window.__rt || !readWorld()) throw new Error('场景运行时不可用，无法返回洞府');
      if (window.__fc?.isGhost) throw new Error('灵魂状态，请先正常还魂再返回洞府');
      if (!caveIsSafe()) {
        const story = state.story;
        closeStoryFishing(); window.qingxiSwordCatch?.cancel?.();
        cancelStoryFlight(story); window.__rt.setFlightVerticalInput?.(0);
        const combat = readCombat();
        if (combat?.active && combat.state === 'fighting' && !combat.paused) act('pause');
        if (caveSettlementPending(combat)) {
          step('等待原生战绩结算后返回洞府');
          const runtime = window.__fc;
          await until(() => {
            if (window.__fc !== runtime) throw new Error('结算期间战斗场景已切换，请核对战绩');
            return !caveSettlementPending(readCombat());
          }, CFG.settlementTimeoutMs, '返回洞府前的原生战绩结算', owner);
        }
        checkOwner(owner);
        // 出错的进行中战斗也正常退出；原生 stop 会记录 quit/defeat，绝不伪报 clear。
        if (readCombat()?.active && !act('stop')) throw new Error('游戏未接受退出当前战斗，已保留暂停保护');
        await until(() => !readCombat()?.active, 10000, '退出当前战斗', owner);
        // stop 触发的原生 quit/defeat 回执也必须先落地，之后才切换场景。
        if (caveSettlementPending(readCombat())) {
          const runtime = window.__fc;
          await until(() => {
            if (window.__fc !== runtime) throw new Error('退出结算期间战斗场景已切换，请核对战绩');
            return !caveSettlementPending(readCombat());
          }, CFG.settlementTimeoutMs, '退出战斗后的原生结算', owner);
        }
        step('任务收尾 · 返回洞府');
        await until(() => {
          const tier = readWorld()?.environment?.tier;
          if (Number.isFinite(tier) && tier !== 4) return true;
          if (!openWorldControls()) return false;
          return clickOnce('worldTownEntry');
        }, 30000, '返回洞府入口', owner);
        // 只确认洞府落地，不再调用 prepareTown 走山门重新入坊。
        await until(caveIsSafe, 420000, '返回洞府场景', owner);
      }
      checkOwner(owner); releaseKeys(); closeWorldControls(); state.navigation = null;
      state.home = { owner, destination:'cave', verified:true, position:myPos()?.slice(), at:Date.now(), error:'' };
    } catch (error) {
      if (owner === state.generation) state.home = { owner, destination:'cave', verified:false, at:Date.now(), error:error.message };
      throw error;
    }
  }
  async function returnCave({preserveError=false} = {}) {
    return workflow(async owner => {
      const originalError = state.error;
      await finishInCave(owner);
      step(originalError ? originalError + '；已返回洞府' : '已返回洞府');
      return '已返回洞府';
    }, {preserveResult:true, preserveError, returnOnError:false});
  }
  async function confirmDailyClaim(id, name, owner) {
    const deadline=Date.now()+60000;
    while (true) {
      let after;
      try { after=await dailyStatus(id,owner); }
      catch (error) {
        if (error instanceof Cancelled || owner !== state.generation) throw error;
        throw new SettlementError(id, '副本领奖回执异常：' + error.message);
      }
      storyCheck(owner); batchDay(after.day);
      if (after.claimed) return after;
      if (Date.now()>deadline) throw new SettlementError(id,name+'通关后未确认领奖');
      await storyWait(1000,owner);
    }
  }
  async function completeDaily() {
    return batchWorkflow('daily',async owner => {
      const blocked = new Set();
      // 已有胜利面板也要先核对结算并退出，不能带着副本去下一处传送阵。
      const previous=readCombat(), previousRow=DAILY.find(row=>row.id===previous?.arena);
      if (previous?.active && !previous.overworld && previous.state==='victory' && previousRow) {
        try {
          await instanceObjective(null,{instance:previousRow.id},owner);
          const receipt=await confirmDailyClaim(previousRow.id,previousRow.name,owner);
          state.batch.completed.push({id:previousRow.id,name:previousRow.name,day:receipt.day});
          state.story.completed++;
          if (!act('stop')) throw new Error('游戏未接受退出副本');
        } catch (error) {
          if (!(error instanceof SettlementError)) throw error;
          await skipSettlement(error,owner);
        }
        blocked.add(previousRow.id);
      }
      for (const row of DAILY) {
        checkOwner(owner);
        const anomaly = combatSettlementError(readCombat());
        if (anomaly && DAILY.some(item => item.id === anomaly.arena) && !blocked.has(anomaly.arena)) {
          await skipSettlement(anomaly, owner); blocked.add(anomaly.arena);
        }
        if (blocked.has(row.id)) continue;
        // 上一场没能装回的法宝，在去下一场之前再试一次。
        if (state.loadout && !state.loadout.hold && !instanceCombatActive() && (await restoreLoadout()).ok) delete state.batch.loadout;
        await storyReady(owner);
        state.batch.current=row.id;
        step('核对今日奖励：' + row.name);
        const status=await dailyStatus(row.id,owner); storyCheck(owner); batchDay(status.day);
        const unavailable=dailyEntrySkip(row,status);
        if (unavailable) {
          state.batch.skipped.push({id:row.id,name:row.name,...unavailable}); continue;
        }
        // 月殿按玩家命中次数校验。被动持续伤害会放大命中，但不是全部失败的唯一原因；
        // 默认按当前装备进场；只有显式启用可选换装 / 被动保护时才收宝或检查跳过。
        let swap=null;
        if (row.id==='moonpalace') {
          if (CFG.moonSwapHitPassive && !instanceCombatActive()) swap=await stripHitPassives(owner);
          const issue=moonEntryIssue(swap);
          if (issue) {
            if (swap?.changed) await finishMoonLoadout(owner);
            state.batch.skipped.push({id:row.id,name:row.name,...issue});
            log('每日副本跳过 '+row.name+'：'+issue.reason); continue;
          }
        }
        try {
          // 导航可能较长，到入口后再核对，以免跨日或用户在另一窗口领取。
          const live=readCombat();
          if (live?.active && !live.overworld && live.state==='fighting' && live.arena!==row.id) throw new Error('请先结束当前副本，再按顺序开始每日副本');
          if (!live?.active || live.overworld || live.arena!==row.id) { await prepareBatchPortal(owner); await travelMenu(INSTANCE_LABELS[row.id],owner); }
          const before=await dailyStatus(row.id,owner); storyCheck(owner); batchDay(before.day);
          const unavailable=dailyEntrySkip(row,before) || (row.id==='moonpalace' && moonEntryIssue(swap));
          if (unavailable) {
            state.batch.skipped.push({id:row.id,name:row.name,...unavailable}); continue;
          }
          try {
            await instanceObjective(null,{instance:row.id},owner);
            await confirmDailyClaim(row.id,row.name,owner);
          } catch (error) {
            if (!(error instanceof SettlementError)) throw error;
            await skipSettlement(error,owner); blocked.add(row.id); continue;
          }
          state.batch.completed.push({id:row.id,name:row.name,day:state.batch.day});
          state.story.completed++;
          if (!act('stop')) throw new Error('游戏未接受退出副本');
        } finally {
          if (swap?.changed) await finishMoonLoadout(owner);
        }
      }
      // 整批结束再核对七场，不能仅凭本地 victory 或先前快照宣称奖励已经到账。
      const rewards = await inspectDailyRewards(owner, state.batch.day);
      state.batch.rewards = rewards;
      for (const completed of [...state.batch.completed]) {
        const receipt = rewards.rows.find(row => row.id === completed.id);
        if (receipt?.claimed === true) continue;
        state.batch.completed.splice(state.batch.completed.indexOf(completed), 1);
        state.story.completed--;
        const error = new SettlementError(completed.id, '副本领奖复查未确认：' + (receipt?.error || completed.name + '仍未领奖'));
        await skipSettlement(error, owner, {exitCombat:false});
      }
      if (state.loadout && !state.loadout.hold && !instanceCombatActive() && (await restoreLoadout()).ok) delete state.batch.loadout;
      return '今日每日副本已处理：完成 '+state.batch.completed.length+' 场，跳过 '+state.batch.skipped.length+' 场；' + dailyRewardSummary(rewards) +
        (state.batch.loadout ? '；' + state.batch.loadout : '');
    });
  }
  // 演练一场每日副本：今日奖励已领（或已用掉）时原生开的就是不发奖励的演练局，打完只看走位与受击。
  // 开局回执若显示本局有奖，就按正常流程打完并核对领奖，不浪费这一局。
  async function drillDaily(id) {
    return batchWorkflow('drill',async owner => {
      const row=DAILY.find(item=>item.id===id || item.name===id);
      if (!row) throw new Error('未知每日副本：'+id+'（可用：'+DAILY.map(item=>item.id).join(' / ')+'）');
      await storyReady(owner);
      state.batch.current=row.id;
      const status=await dailyStatus(row.id,owner); storyCheck(owner); batchDay(status.day);
      if (!status.hasKey) return row.name+'未解锁，无法演练';
      let swap=null;
      if (row.id==='moonpalace' && CFG.moonSwapHitPassive && !instanceCombatActive()) swap=await stripHitPassives(owner);
      try {
        const issue=row.id==='moonpalace' && moonEntryIssue(swap);
        if (issue) return issue.reason;
        const live=readCombat();
        if (!live?.active || live.overworld || live.arena!==row.id) { await prepareBatchPortal(owner); await travelMenu(INSTANCE_LABELS[row.id],owner); }
        const beforeEntry=row.id==='moonpalace' && moonEntryIssue(swap);
        if (beforeEntry) return beforeEntry.reason;
        const damageBefore=state.damage.length ? state.damage[state.damage.length-1].time : -Infinity;
        let result;
        try { result=await instanceObjective(null,{instance:row.id,drill:true},owner); }
        catch (error) {
          if (!(error instanceof SettlementError)) throw error;
          await skipSettlement(error,owner); return row.name+'演练中止：'+error.message;
        }
        const hits=state.damage.filter(d=>d.time>damageBefore && d.arena===row.id);
        const lost=Math.round(hits.reduce((sum,d)=>sum+(d.hpBefore-d.hp),0));
        const summary=row.name+(result?.drill?'演练':'有奖局')+'结束：'+({victory:'通关',defeat:'战败'}[result?.outcome] || result?.kind || '完成')+
          (result?.seconds!=null?' · '+result.seconds+' 秒':'')+' · 受击 '+(result?.enemyHits ?? hits.length)+' 次'+(hits.length?'，掉血 '+lost:'');
        if (!result?.drill) { await confirmDailyClaim(row.id,row.name,owner); state.batch.completed.push({id:row.id,name:row.name,day:state.batch.day}); }
        state.batch.drill={id:row.id,name:row.name,...(result?.drill?result:{kind:result?.kind || null,drill:false}),hits:hits.length,lost};
        if (!act('stop')) throw new Error('游戏未接受退出副本');
        return summary;
      } finally {
        if (swap?.changed) await finishMoonLoadout(owner);
      }
    });
  }
  function trialArenaLive(live, arena) { return !!live?.active && !live.overworld && live.arena===arena; }
  function noteTrialOrphan(arena, run) {
    const seen=state.batch.orphans ||= [], id=run?.id || null;
    if (seen.some(row=>row.arena===arena && row.runId===id)) return;
    seen.push({arena,runId:id,runArena:run?.arena || null,at:Date.now()});
    log(TRIALS[arena]+'：服务端有未结算旧局'+(id?'（'+id+'）':'')+'，无法在场外校验，入场查看本局提示');
  }
  function ownedTrial(quota, arena = quota.arena) {
    const live=readCombat(), run=quota.activeRun;
    return run?.id && live?.active && !live.overworld && ['fighting','defeat','victory'].includes(live.state) &&
      Object.hasOwn(TRIALS,live.arena) && run.arena===arena && run.arena===live.arena && live.trial?.run?.id===run.id ? live : null;
  }
  async function completeTrials() {
    return batchWorkflow('trials',async owner => {
      state.batch.tier = 1;
      hookDailyTrialStart(window.__fc);
      let index=0; const blocked = new Set();
      try { while (true) {
        checkOwner(owner);
        const current=readCombat();
        if (current?.active && Object.hasOwn(TRIALS,current.arena) && !blocked.has(current.arena) &&
          (current.trial?.syncError || current.trial?.anomaly)) {
          await skipSettlement(new SettlementError(current.arena,'战绩同步异常：'+(current.trial.syncError || current.trial.anomaly),current),owner);
          blocked.add(current.arena); index=current.arena==='longwind'?1:0;
        }
        await storyReady(owner);
        const quotas={};
        for (const arena of Object.keys(TRIALS).filter(id=>!blocked.has(id))) {
          const quota=await trialEntryQuota(owner,arena); storyCheck(owner); if (quota.day) batchDay(quota.day);
          if (quota.activeRun && !ownedTrial(quota,arena)) {
            const live=readCombat();
            if (trialArenaLive(live,arena)) {
              const error=new SettlementError(arena,'服务端仍有未结算试炼，本次跳过该场地',live);
              error.runId=quota.activeRun.id || error.runId;
              await skipSettlement(error,owner,{exitCombat:true}); blocked.add(arena); continue;
            }
            // 原生在波次同步失败后不再发送结束请求，旧局会一直挂在服务端（两个场地的查询都能看到）。
            // 这里没有对应的本地战斗，无法在场外校验；不封锁场地，入场后以本局原生回执为准。
            noteTrialOrphan(arena,quota.activeRun); quota.orphan=true;
          }
          const live=readCombat();
          if (live?.active && live.arena===arena && live.state==='defeat' && !ownedTrial(quota,arena)) {
            await skipSettlement(new SettlementError(arena,'失败试炼未确认有效 run，本次不重试',live),owner);
            blocked.add(arena); continue;
          }
          quotas[arena]=quota;
        }
        state.batch.quotas={};
        for (const [id,quota] of Object.entries(quotas)) updateBatchTrialQuota(id,quota);
        if (!Object.keys(quotas).length) state.batch.remaining=0;
        // 通关面板可能仍在，但服务端 activeRun 已清空；先核对本场，不重新开局。
        const terminal=readCombat();
        const settled=terminal?.active && !terminal.overworld && terminal.state==='victory' &&
          quotas[terminal.arena] && terminal.trial?.run ? terminal : null;
        const existing=Object.entries(quotas).map(([id,q])=>ownedTrial(q,id)).find(Boolean) || settled;
        if (state.batch.remaining===0 && !existing) return blocked.size
          ? '本次有奖试炼已处理：通关 '+state.batch.completed.length+' 场，结算异常跳过 '+state.batch.skipped.length+' 场；异常场地本次不重试'
          : '今日有奖试炼次数已用完：本次通关 '+state.batch.completed.length+' 场，麦田/竹林剩余均为 0 次';
        const order=index%2?['bamboo','longwind']:['longwind','bamboo'];
        const arena=existing?.arena || order.find(id=>quotas[id] && (quotas[id].unknown || quotas[id].remaining>0)), quota=quotas[arena]; state.batch.current=arena;
        // 次数只作导航参考；是否发奖以本局原生入场回执为准。
        const beforeRemaining=quota.unknown?null:quota.remaining+(existing?1:0);
        if (!existing && (state.batch.entryAttempts || 0)>=CFG.trialEntryMaxAttempts)
          return '本次试炼入场检查已达上限，停止本轮；本次有奖通关 '+state.batch.completed.length+' 场';
        if (!existing) state.batch.entryAttempts=(state.batch.entryAttempts || 0)+1;
        if (!existing) {
          const previousPrompt=trialEntryPrompt();
          await prepareBatchPortal(owner); await travelMenu(arena==='bamboo'?'竹林试炼':'麦田试炼',owner);
          const live=readCombat();
          if (live?.active && !live.overworld && live.arena===arena) await verifyTrialEntry(arena,owner,previousPrompt,false);
        }
        const entryQuota=await trialEntryQuota(owner,arena); storyCheck(owner); if (entryQuota.day) batchDay(entryQuota.day);
        const entered=ownedTrial(entryQuota,arena) || (!entryQuota.activeRun && existing?.arena===arena && existing.state==='victory' ? existing : null);
        // 尚未开局时查到的仍是残留旧局：继续走原生开局，由入场检查确认本局是否有奖。
        const entryLive=readCombat();
        const orphanOnly=!!entryQuota.activeRun && !entered && !existing && !trialArenaLive(entryLive,arena);
        // 已开局且原生回执确认了本局有奖 run，而服务端查询仍只报入场前就记下的那个旧局：以本局回执为准。
        const beside=!!entryQuota.activeRun && !entered && !existing && trialArenaLive(entryLive,arena) &&
          entryLive.trial?.run?.rewarded===true && !!entryLive.trial.run.id && entryLive.trial.run.id!==entryQuota.activeRun.id &&
          (state.batch.orphans || []).some(row=>row.runId && row.runId===entryQuota.activeRun.id);
        if (orphanOnly || beside) noteTrialOrphan(arena,entryQuota.activeRun);
        else if ((entryQuota.activeRun && (!entered || entered.arena!==arena)) || (existing && !entered)) {
          const live=readCombat();
          const error=new SettlementError(arena,'入场前仍有未结算试炼，本次跳过该场地',live?.arena===arena?live:null);
          error.runId=entryQuota.activeRun?.id || error.runId;
          await skipSettlement(error,owner,{exitCombat:live?.arena===arena}); blocked.add(arena); index=arena==='longwind'?1:0; continue;
        }
        updateBatchTrialQuota(arena,entryQuota);
        if (entryQuota.remaining===0 && !entered) {
          const live=readCombat();
          if (!live?.active || live.overworld || live.arena!==arena) continue;
        }
        let started,result,after;
        try {
          if (entered && ['victory','defeat'].includes(entered.state)) { await verifyTrialEntry(arena,owner); startDriving(arena); }
          else await beginTrial(arena,owner);
          storyCheck(owner); state.retry={enabled:true,attempts:1,history:[]};
          started=readCombat();
          await until(() => !state.running,180*60000,'有奖试炼通关',owner);
          storyCheck(owner); result=state.result;
          if (result?.code==='settlement_anomaly') throw new SettlementError(arena,state.error || '试炼结算异常');
          if (state.error) throw new Error(state.error);
          if (!result?.verified || result.outcome!=='victory' || result.rewarded!==true) throw new SettlementError(arena,'试炼尚未确认有奖通关');
          // 开局前次数可校验时，通关后仍必须查到次数；只有开局前就无法校验的入场检查局才放宽。
          try { after=beforeRemaining==null ? await trialEntryQuota(owner,arena) : await trialStatus(owner,arena); }
          catch (error) {
            if (error instanceof Cancelled || owner !== state.generation) throw error;
            throw new SettlementError(arena,'试炼结算次数查询异常：'+error.message);
          }
          storyCheck(owner); if (after.day) batchDay(after.day);
          // 放宽时以本局原生回执（有奖 run + 已结算通关）为准；
          // 查询失败时的 activeRun 只是战斗内缓存，不能据此否定已确认的通关。
          // 只有本局自己的 run 仍未结算才算异常；查到的若是别的残留旧局，与本局通关无关。
          const ownRunId=result.runId || started?.trial?.run?.id || null;
          const unsettled=after.activeRun && !after.error && (!after.activeRun.id || !ownRunId || after.activeRun.id===ownRunId);
          if (after.activeRun && !after.error && !unsettled) noteTrialOrphan(arena,after.activeRun);
          if (unsettled || (beforeRemaining!=null && after.remaining>=beforeRemaining))
            throw new SettlementError(arena,'试炼结算后有奖次数未确认');
          if (after.unknown) log(TRIALS[arena]+'通关后次数仍无法校验，按本局有奖回执记为完成');
        } catch (error) {
          if (error instanceof TrialPractice) return trialPracticeSummary(error);
          if (!(error instanceof SettlementError)) throw error;
          await skipSettlement(error,owner); blocked.add(arena); index=arena==='longwind'?1:0; continue;
        }
        state.batch.completed.push({id:arena,name:TRIALS[arena],day:after.day || state.batch.day,tier:result.tier,runId:result.runId || started?.trial?.run?.id || null,result});
        updateBatchTrialQuota(arena,after);
        index=arena==='longwind'?1:0;
        if (!act('stop')) throw new Error('游戏未接受退出试炼');
      } } catch (error) {
        if (error instanceof TrialPractice) return trialPracticeSummary(error);
        throw error;
      }
    });
  }
  function ensureMoonWheel() {
    if (!state.story || state.story.moonWheelChecked) return;
    state.story.moonWheelChecked=true;
    if (fieldEnvironment?.swordSkill?.('wheelOn')===false) act('shieldWheel');
  }
  // 月殿只观察原生状态，用当前角色装备对应的正常战斗命令输出；命中数不参与停火决策。
  function moonSword(s) {
    return (s.kit||[]).find(k=>!k.locked && (k.skill==='qingzhu' || !k.skill&&k.category==='sword') && Number.isFinite(k.cost));
  }
  function moonGiant(s) {
    const sword=moonSword(s), math=window.QingxiGiantSwordMath, rule=window.QingxiTreasureRules?.giant;
    if (!sword || !math || !rule || !(rule.cost>0) || !(math.reach>0)) return null;
    return {cost:sword.cost/8*rule.cost,range:Math.min(rule.range||14,math.reach,12)-.2,
      impact:Number.isFinite(math.strike)?math.strike:1.1};
  }
  function moonManaScale(s) {
    // 术法与法宝的基础消耗不同；优先读取当前角色的原生境界倍率，不要求装备青竹剑。
    try {
      const scale=window.__rt?.mobileHudState?.()?.profile?.combat?.manaCostScale;
      if (Number.isFinite(scale) && scale>0) return scale;
    } catch (e) {}
    const sword=moonSword(s);
    if (sword?.cost>0) return sword.cost/8;
    const rules=window.QingxiTreasureRules;
    for (const slot of s.kit||[]) {
      const rule=rules?.skills?.[slot.skill] || rules?.templates?.[slot.category];
      const base=rule && Math.round(rule.cost*(Number(slot.costScale)||1+.08*(Number(slot.tier)||0)));
      if (!slot.locked && Number.isFinite(slot.cost) && slot.cost>0 && base>0) return slot.cost/base;
    }
    return 1;
  }
  function moonPreferredArt(s) {
    const rules=window.QingxiTreasureRules?.arts;
    return (s.arts||[]).filter(name=>rules?.[name]?.damage>0 && rules[name].cost>=0 &&
      (name!=='血煞斩' || s.hp/Math.max(1,s.hpMax)>.5))
      .sort((a,b)=>((rules[a].cast||0)>1.2)-((rules[b].cast||0)>1.2) ||
        rules[b].damage/Math.max(1,rules[b].cost)-rules[a].damage/Math.max(1,rules[a].cost))[0] || null;
  }
  function moonSkills(s, target) {
    const distance=dist(myPos(),target.position), rules=window.QingxiTreasureRules;
    const choices=(s.kit||[]).flatMap(slot=>{
      const rule=rules?.skills?.[slot.skill] || rules?.templates?.[slot.category];
      // 青竹剑走点杀兜底；其余法宝按实际技能位轮换。按真实攻击范围筛选，不空放玄天刀路。
      if (slot.skill==='qingzhu' || !slot.skill && slot.category==='sword') return [];
      const range=slot.skill==='xuantian'?10:slot.skill==='chisi'?19:rule?.range;
      if (!rule || slot.locked || !Number.isInteger(slot.slot) || slot.slot<1 || slot.slot>6 ||
        !Number.isFinite(slot.cost) || slot.cost<0 || !(rule.damage>0 || rule.dot>0) || !(range>distance+.5) ||
        rule.hpPct>0 && s.hp/Math.max(1,s.hpMax)<=.5) return [];
      return [{kind:'slot',command:'slot'+slot.slot,counter:'treasures',name:slot.name||slot.skill,slot,cost:slot.cost,
        identity:[slot.itemId||'',slot.skill||'',slot.name||''].join('|'),
        cooldown:slot.cooldown??s.cooldown?.['slot'+slot.slot]??0,duration:s.empowered?0:rule.cast||0}];
    }).sort((a,b)=>a.slot.slot-b.slot.slot);
    const name=s.art, rule=rules?.arts?.[name], index=(s.arts||[]).indexOf(name);
    const artRange=name==='烈焰焚天'?8.5:name==='灵兽突袭'?13:23;
    if (rule?.damage>0 && rule.cost>=0 && index>=0 && distance<artRange &&
      (name!=='血煞斩' || s.hp/Math.max(1,s.hpMax)>.5)) {
      const left=name==='火弹' ? s.cooldown?.spell : s.cooldown?.['art'+index];
      choices.push({kind:'art',command:'art',counter:'arts',name,identity:name,cost:moonManaScale(s)*rule.cost,
        cooldown:left??0,duration:s.empowered?0:rule.cast||0});
    }
    return choices;
  }
  function moonCombatFrame(s, target, step, boundary, now) {
    if (!state.story || s.arena!=='moonpalace') return false;
    const view=window.qingxiNgqBoss?.stats?.(), me=myPos();
    if (!view || !me || !['intro','fight','dying'].includes(view.mode) || s.mechanicTargets?.length) return false;
    const watch=moonEvasionWatch(s,me,now,view);
    const clock=Number.isFinite(s.wave?.time)?s.wave.time:now/1000, run=s.moonPalace?.run?.id||target.id;
    let plan=state.story.moonCombat;
    if (!plan || plan.run!==run || clock<plan.time || view.phase<plan.phase || (view.counts?.interrupts||0)<plan.interrupts) {
      plan=state.story.moonCombat={run,time:clock,phase:view.phase,interrupts:view.counts?.interrupts||0,
        channels:view.counts?.channels||0,attempts:0,nextChannelAt:clock,artSwitchAt:-Infinity,artSwitches:0,
        channel:null,cast:null,lastCastPosition:null,lastSkill:null,hp:s.hp,hurtAt:-Infinity,status:'月殿走位'};
    }
    if (view.phase===3 && plan.phase!==3) plan.nextChannelAt=clock+9.5;
    if ((view.counts?.interrupts||0)>plan.interrupts) {
      log('月殿：原生确认血契打断（'+view.counts.interrupts+'次）');
      plan.status='原生确认血契打断';
    }
    if (s.hp<plan.hp) plan.hurtAt=clock;
    plan.hp=s.hp;plan.time=clock;plan.phase=view.phase;
    plan.interrupts=view.counts?.interrupts||0;plan.channels=view.counts?.channels||0;
    if (view.channel==null && plan.channel) {plan.channel=null;plan.nextChannelAt=clock+17.5;}
    const d=dist(me,target.position), giant=moonGiant(s), sword=moonSword(s);
    const choices=moonSkills(s,target), previous=choices.findIndex(c=>c.command===plan.lastSkill);
    const rotation=previous<0?choices:[...choices.slice(previous+1),...choices.slice(0,previous+1)];
    const ready=rotation.filter(c=>c.cooldown===0 && now>=(state.skillRetryAt[c.command]||0));
    plan.hits=Number.isFinite(s.metrics?.playerHits)?s.metrics.playerHits:null;
    const nav=reason=>{plan.status=reason;state.navigation={mode:plan.cast?'casting':'combat',distance:d,reason,updatedAt:Date.now()};};
    if (s.paused || s.airborne || document.hidden || window.__rt?.mobileHudState?.()?.blocked) {plan.cast=null;releaseKeys();return true;}
    // 先补药，但绝不落入通用点杀优先 / 分化 / 术法兜底。
    attack(s,null,now,true,true);
    const move=(toward=null)=>{
      let heading=toward??(step.mobile||step.critical?step.want:bearingTo(me,target.position)+state.orbitDir*(Math.PI/2+Math.max(-.9,Math.min(.9,(12.8-d)*.12))));
      if (toward==null && d>25 && !step.mobile && !step.critical) heading=bearingTo(me,target.position);
      const hazards=[step.threat?.hazard,...groundHazards()].filter(Boolean);
      steerWalkable(heading,boundary,hazards.length?hazards:null);
      return dodgeThreat(s,step,now);
    };
    const incoming=Object.values(view.swords||{}).some(blade=>blade?.attacking);
    const danger=step.critical || step.mobile || s.hp/Math.max(1,s.hpMax)<.35 || clock-plan.hurtAt<.7;
    // 关键帧先走位和翻滚，避免同帧的锁定、站定或血契等待覆盖避险动作。
    if (step.roll) {plan.cast=null;move();nav('翻滚避开落剑 / 血契 / 血池，随后继续输出');return true;}
    if (s.target!==target.id) {
      plan.cast=null;move();nav('先锁定南宫阙');
      if (now-state.lastLockAt>450) {state.lastLockAt=now;act('lock');}
      return true;
    }
    if (view.channel!=null) {
      plan.cast=null;
      const channel=plan.channel ||= {at:clock,attempts:0,lastAt:-Infinity,gapClosed:false};
      const unsafe=step.threat && step.threat.move!=='ngq-channel' || incoming;
      if (unsafe || view.channel>=2.65) {move();nav('血契危险优先退避，未确认打断不报成功');return true;}
      if (s.casting || s.action?.kind==='cast') {move();nav('血契出现，移动取消读条后打断');return true;}
      if (s.giantSword && clock-channel.lastAt<1.15) {
        // 留住不足一息的斩击窗口，避免玩家后退令首领追来、离开已锁定的刀路。
        if (clock-plan.hurtAt<.7) move(); else releaseKeys();
        nav('等待巨剑刀刃接触 / 原生血契打断');return true;
      }
      // H 巨剑术是原生瞬发、14 米内真实刀刃重击；追到 12 米内再出手，更容易命中固定刀路。
      if (giant && (d<=giant.range || s.hp/Math.max(1,s.hpMax)>=.35) && (s.cooldown?.giant??0)===0 && !s.giantSword && s.mana>=giant.cost && view.channel+giant.impact+.2<3) {
        if (d>giant.range) {move(bearingTo(me,target.position));nav('接近巨剑真实斩击范围');return true;}
        releaseKeys();
        if (!s.casting && !(s.gcdLeft>0) && (!s.action || s.action.kind==='cast') && clock-channel.lastAt>.8 && channel.attempts<2 &&
          combatAction('swords','giants',now)) {
          channel.lastAt=clock;channel.attempts++;plan.attempts++;
          nav('巨剑重击已出，等待原生血契打断计数');state.lastAct='月殿 · 巨剑打断血契';log('月殿：巨剑重击打断血契（等待原生确认）');
        }
        return true;
      }
      if (s.hp/Math.max(1,s.hpMax)<.35 && d>2.4) {move();nav('低血不追近突刺，保留血契末段闪避');return true;}
      // 灵力/巨剑冷却不足才追近突刺。保留动画，不逐帧重启；一次出手不当作已打断。
      if (s.action?.kind==='attack' && ['thrust','heavy'].includes(s.action.clip)) {releaseKeys();nav('等待突刺接触 / 原生打断');return true;}
      if (d>2.4) {
        move(bearingTo(me,target.position));nav('追近突刺打断血契');
        if (d>6 && view.channel<1.2 && !channel.gapClosed && s.stamina>=50 && !s.action && !s.casting) {
          channel.gapClosed=dodgeThreat(s,{roll:true},now);
        }
        return true;
      }
      releaseKeys();
      if (!s.casting && !(s.gcdLeft>0) && !s.action && s.stamina>=20 && clock-channel.lastAt>1 && channel.attempts<2 && combatAction('thrust',null,now)) {
        channel.lastAt=clock;channel.attempts++;plan.attempts++;state.lastAct='月殿 · 近身突刺';
      }
      nav('近身突刺，等待原生血契打断');return true;
    }
    // 切到最适合的术法（原生 nextArt，与长按 V 相同）；读条中或切换未生效时不反复切。
    const wanted=moonPreferredArt(s);
    if (wanted && s.art!==wanted && !s.casting && !plan.cast && plan.artSwitches<12 && now-plan.artSwitchAt>700) {
      plan.artSwitchAt=now;plan.artSwitches++;
      if (act('nextArt')) log('月殿：切换术法，目标 '+wanted);
    }
    // 血契临近时不新开跨过窗口的长读条，但不为尚未出现的血契冻结灵力和正常输出。
    const canFinish=duration=>(view.phase!==3 || !(plan.nextChannelAt>clock) || clock+duration+.5<plan.nextChannelAt) &&
      !(view.phase===3 && watch && (watch.fightTime<watch.unknownUntil || watch.nextPoolAt!=null && watch.nextPoolAt-watch.fightTime>=-.4 && watch.nextPoolAt-watch.fightTime<duration+.4));
    // 域内无法从只读 stats 得知亮起哪半边，常态保持 12.8 米；血池期间每次短读条后换落点。
    const castSafe=!danger && !incoming && d>=11.5 && view.mode==='fight' &&
      !moonHazardZones(watch).some(h=>dist(me,h.center)<h.radius) &&
      (!view.pools || clock-plan.hurtAt>1) && (!plan.lastCastPosition || dist(me,plan.lastCastPosition)>3);
    if (plan.cast) {
      if (!castSafe || !canFinish(Math.max(0,plan.cast.duration-(now-plan.cast.at)/1000))) {plan.cast=null;move();nav('预警 / 受伤出现，移动取消读条');return true;}
      if (s.casting) {releaseKeys();nav('安全空档读条');return true;}
      if (plan.cast.fired || now-plan.cast.at>900) {plan.lastCastPosition=me.slice();plan.cast=null;move();return true;}
      const choice=choices.find(c=>c.command===plan.cast.command), position=me.slice();
      const still=dist(position,plan.cast.position)<.03;plan.cast.position=position;releaseKeys();
      if (!still || now-plan.cast.at<90) return true;
      if (!choice || choice.identity!==plan.cast.identity || choice.cooldown>0 || s.mana<choice.cost || s.gcdLeft>0 || s.action && s.action.kind!=='cast') {plan.cast=null;move();return true;}
      plan.cast.fired=combatAction(choice.command,choice.counter,now);
      if (!plan.cast.fired) {plan.cast=null;move();return true;}
      plan.lastSkill=choice.command;state.lastAct='月殿 · '+choice.name;log('月殿空档输出 '+choice.name);return true;
    }
    // 满气势的大招不耗灵力，安全时优先使用。
    if (!danger && !incoming && view.mode==='fight' && castUltimate(s,{e:target,d},now)) {move();nav('气势已满，施放剑阵');return true;}
    let moved=false, rolled=false;
    const moveOnce=()=>{if (!moved) {rolled=move();moved=true;} return rolled;};
    if (!s.casting && !(s.gcdLeft>0) && (!s.action || s.action.kind==='cast') && view.mode==='fight') {
      for (const chosen of ready) {
        if (s.mana<chosen.cost || (chosen.duration===0?step.critical:!castSafe || !canFinish(chosen.duration))) continue;
        if (!chosen.duration) {
          if (moveOnce()) {nav('翻滚避险，随后继续输出');return true;}
          if (!combatAction(chosen.command,chosen.counter,now)) continue;
          plan.lastSkill=chosen.command;state.lastAct='月殿 · '+chosen.name;
          nav('按技能栏轮换输出 '+chosen.name);return true;
        }
        releaseKeys();plan.cast={command:chosen.command,identity:chosen.identity,duration:chosen.duration,at:now,position:me.slice(),fired:false};
        nav('站稳后施放 '+chosen.name);return true;
      }
    }
    moveOnce();
    // 当前可用法宝 / 术法都未能释放时，用付得起的点杀持续补伤，不按累计命中数或未来技能消耗停火。
    nav('绕行并使用当前灵力可施放的技能');
    if (!rolled && !s.casting && !(s.gcdLeft>0) && view.mode==='fight' &&
      s.cooldown?.pierce===0 && sword && s.mana>=sword.cost && d<32 && combatAction('pierce','pierces',now)) {
      state.lastAct='月殿 · 点杀补伤';
    }
    return true;
  }

  function goldMirror(s, now) {
    const seen=state.story.mirrorSeen ||= {};
    for (const mirror of s.mechanicTargets || []) {
      const old=seen[mirror.id];
      if (!old || old.state!==mirror.state) seen[mirror.id]={state:mirror.state,since:now,observed:!!old};
    }
    // 金光持续 2.6 秒；只在观察到变金后的前 0.9 秒出手，飞剑到达时仍是金光。
    return (s.mechanicTargets || []).filter(m => m.state==='gold' && seen[m.id]?.observed && now-seen[m.id].since<900)
      .sort((a,b) => dist(myPos(),a.position)-dist(myPos(),b.position))[0];
  }
  async function moonMechanicFrame(s, owner) {
    observeMoonDiagnostics(s);
    if (s.paused || document.hidden) {releaseKeys();return true;}
    const me=myPos();
    const runes=(window.__fc.mechanicInteractions?.() || []).filter(t => t.data?.kind==='boss-rune');
    if (runes.length) {
      // 三枚阵纹的普通 E 交互；选择最短完整路线，不伪造镇压结果。
      const point=t => Array.isArray(t.point)?t.point:[t.point.x,t.point.y,t.point.z];
      const routes=items => items.length ? items.flatMap((item,i) => routes(items.filter((_,j)=>j!==i)).map(rest=>[item,...rest])) : [[]];
      const length=route => route.reduce((sum,t,i)=>sum+dist(i?point(route[i-1]):me,point(t)),0);
      const route=routes(runes).sort((a,b)=>length(a)-length(b))[0],target=route[0];
      const view=window.qingxiNgqBoss?.stats?.(), round=view?.counts?.runeRounds??0;
      let marker=state.story.moonRunes;
      if (!marker || marker.round!==round) marker=state.story.moonRunes={round,attempts:0,confirmed:0,status:'靠近原生阵纹'};
      if (marker.target!==target.data.id) observeMoonDiagnostics(s,'rune_target',{target:target.data.id,
        route:route.map(t=>t.data.id),routeLength:moonNumber(length(route))});
      marker.target=target.data.id;
      marker.confirmed=Array.isArray(view?.runes)?view.runes.filter(value=>value==='sealed').length:marker.confirmed;
      if (state.story.moonCombat) state.story.moonCombat.cast=null;
      step('月殿镇魂：已确认 '+marker.confirmed+' / 3 · 靠近 '+(target.data.name||target.data.id));
      await walkTo(point(target),1.25,owner,{flight:false,defend:false,moonEvasion:true,interrupt:()=>readCombat()?.state!=='fighting' || !(window.__fc.mechanicInteractions?.() || []).some(t=>t.data?.id===target.data.id)});
      storyCheck(owner);
      const near=dist(myPos(),point(target))<1.5,available=(window.__fc.mechanicInteractions?.() || []).some(t=>t.data?.id===target.data.id);
      observeMoonDiagnostics(readCombat(),'rune_arrival',{target:target.data.id,near,available,distance:moonNumber(dist(myPos(),point(target)))});
      if (near && available) {
        marker.attempts++;
        const before=moonDiagnosticSnapshot(readCombat()),accepted=act('interact');
        const attempt={target:target.data.id,attempt:marker.attempts,accepted,before};
        const diagnostic=observeMoonDiagnostics(readCombat(),'rune_interact',attempt);
        if (diagnostic) diagnostic.lastAttempt={target:target.data.id,attempt:marker.attempts,accepted,at:Date.now()};
        // 原生 interact 在没靠够近时也会返回 true；只认原生 sealed，不把接受指令当成镇压成功。
        const after=window.qingxiNgqBoss?.stats?.();
        marker.confirmed=Array.isArray(after?.runes)?after.runes.filter(value=>value==='sealed').length:marker.confirmed;
        marker.status=marker.confirmed===3?'原生确认三枚已镇压':'等待原生镇压确认';
        if (JSON.stringify(before.runes)===JSON.stringify(after?.runes))
          observeMoonDiagnostics(readCombat(),'rune_unconfirmed',{target:target.data.id,attempt:marker.attempts,accepted,note:'指令已返回，但原生阵纹未发生 sealed 变化'});
        step('月殿镇魂：已确认 '+marker.confirmed+' / 3 · '+marker.status);
      }
      return true;
    }
    // 三纹已封但原生还没切到 dying，或目标列表尚未刷新时，不对免疫首领补输出。
    if (window.qingxiNgqBoss?.stats?.()?.mode==='finale') {
      if (moonDiagnosticContext && Date.now()-(moonDiagnosticContext.lastMissingAt||0)>=500) {
        moonDiagnosticContext.lastMissingAt=Date.now();
        observeMoonDiagnostics(s,'rune_targets_missing',{note:'原生仍为 finale，交互目标为空；等待原生刷新'});
      }
      releaseKeys();step('月殿镇魂：等待原生阵纹 / 镇压收束');return true;
    }
    if (s.mechanicTargets?.length) {
      const a=captureArena(s,me), boundary={center:a.center,radius:Math.max(5,a.radius-4)};
      const mirror=goldMirror(s,Number.isFinite(s.wave?.time)?s.wave.time*1000:performance.now());
      const waiting=(s.mechanicTargets||[]).slice().sort((a,b)=>dist(me,a.position)-dist(me,b.position))[0];
      const target=mirror || waiting;
      if (target) {
        recordDamage(s,performance.now());
        const evasion=steeringTarget(s,me,performance.now());
        // 靠近宝镜缩短飞剑飞行时间；红镜仅作走位目标，绝不攻击。
        steerWalkable(evasion.mobile||evasion.critical?evasion.want:
          bearingTo(me,target.position)+state.orbitDir*(Math.PI/2+Math.max(-1.15,Math.min(1,(6-dist(me,target.position))*.2))),boundary,
          [evasion.threat?.hazard,...groundHazards()].filter(Boolean));
        dodgeThreat(s,evasion,performance.now());attack(s,null,performance.now(),true,true);
      } else releaseKeys();
      if (mirror) {
        // 只向已锁定的金光镜点杀，避免范围术法或锁定前的大招误中红镜。
        if (s.target!==mirror.id) {
          if (performance.now()-state.lastLockAt>180) { state.lastLockAt=performance.now(); act('lock'); }
        } else if (!s.airborne && dist(me,mirror.position)<=14 && !(s.gcdLeft>0) && !s.casting && s.cooldown?.pierce===0 && s.mana>=(moonSword(s)?.cost??CFG.pierceMana)) {
          if (act('pierce')) state.stats.pierces++;
        }
      }
      return true;
    }
    if (state.story) state.story.mirrorSeen=null;
    return false;
  }

  // ============ 10. 对外控制台接口 ============
  window.__autobot = {
    version: '2.0.78', state,
    returnCave, returnTown:returnCave, enter: () => workflow(async owner => { await prepareTown(owner); return '已进入青溪坊'; }),
    start: startTrial, daily: completeDaily, drill: drillDaily, checkDailyRewards, trials: completeTrials, mainline: runStory, story: runStory, inspectMainline: mainline,
    sidequests: options => runStory({...options,kind:'全部支线'}), inspectSidequests: () => mainline('全部支线'),
    yufeng: options => runStory({...options,kind:'御风车支线'}), inspectQuests: (kind='全部任务')=>mainline(kind), taskKinds, rewardLog: () => ({...rewardLogStatus}),
    journal:collectJournal, inspectJournal, journalPoints:journalRows,
    loadoutCheck, restoreLoadout, moonDiagnostics, showMoonDiagnostics,
    quests: questRows, markQuest, interactQuest,
    stop() {
      state.retry.enabled = false;
      if (state.batch?.status === 'running') state.batch.status='cancelled';
      if (state.journal?.status === 'running') state.journal.status='cancelled';
      ++state.generation; state.busy = false;
      if (state.story) {
        closeStoryFishing(); window.qingxiSwordCatch?.cancel?.();
        document.querySelector('.jz-stone [data-close]')?.click();
        cancelStoryFlight(state.story);
        if (typeof state.story.camera === 'boolean') state.story.runtime.setCameraFollowEnabled?.(state.story.camera);
        state.story = null;
      }
      stopDriving(); step('已停止'); return '已停止';
    },
    resume() {
      if (state.busy) return '正在准备，请等待或先停止';
      const s = readCombat();
      if (!s?.active || s.overworld || !s.wave || s.state !== 'fighting') return '当前没有进行中的试炼';
      if (!Object.hasOwn(TRIALS, s.arena)) return '此剧情副本需要专用机制，暂不支持自驾';
      if (state.running) return '已在运行';
      if (s.trial?.run?.rewarded!==true) return checkedTrialControl(s.arena);
      startDriving(s.arena); return '已恢复';
    },
    untilVictory() {
      if (state.busy || state.story) return '请先结束当前主线流程';
      const s = readCombat();
      if (!s?.active || s.overworld || !s.wave || !Object.hasOwn(TRIALS,s.arena) || !['fighting','defeat'].includes(s.state)) return '请先进入麦田或竹林试炼';
      if (s.trial?.syncError || s.trial?.anomaly || s.trial?.syncing > 0) return '请先处理战绩同步状态';
      if (state.retry.enabled) return '连续重试已开启';
      if (s.trial?.run?.rewarded!==true) return checkedTrialControl(s.arena,true);
      if (s.state === 'defeat' && !act('retry')) return '游戏未接受本波重试';
      state.retry = {enabled:true,attempts:1,history:[]};
      if (!state.running) startDriving(s.arena);
      return '连续重试已开启';
    },
    testMove(degWorld) { return steer(degWorld * Math.PI / 180); },
    report() {
      const combat = readCombat(), flight = readWorld()?.flight;
      return { phase: state.phase, error: state.error, running: state.running, busy: state.busy,
        story: state.story ? { current: state.story.current, completed: state.story.completed, deadline: state.story.deadline,
          moonCombat:state.story.moonCombat ? {status:state.story.moonCombat.status,attempts:state.story.moonCombat.attempts,channels:state.story.moonCombat.channels,
            interrupts:state.story.moonCombat.interrupts,hits:state.story.moonCombat.hits ?? null,lastSkill:state.story.moonCombat.lastSkill ?? null} : null,
          muMechanic:state.story.muMechanic ? {...state.story.muMechanic} : null } : null,
        questSummary: state.questSummary ? JSON.parse(JSON.stringify(state.questSummary)) : null,
        batch: state.batch ? JSON.parse(JSON.stringify(state.batch)) : null,
        dailyRewards: state.dailyRewards ? JSON.parse(JSON.stringify(state.dailyRewards)) : null,
        trialEntry: state.trialEntry ? {...state.trialEntry} : null,
        instanceHistory: instanceHistory.slice(-12).map(row=>({...row})),
        moonDiagnostics:moonDiagnostics(),
        waveHome: state.waveHome ? {arena:state.waveHome.arena,point:state.waveHome.point.slice(),source:state.waveHome.source} : null,
        hitAudit: hitAudit(),
        loadout: {pending:state.loadout ? {...state.loadout,items:state.loadout.items.map(item=>({...item})),order:state.loadout.order.slice()} : null,
          last:state.loadoutLast ? {...state.loadoutLast} : null, check:state.loadoutCheck ? {...state.loadoutCheck} : null},
        retry: {...state.retry,history:state.retry.history.slice()},
        stats: { ...state.stats }, last: state.lastAct, arena: arenaInfo, collisionReady:!!fieldEnvironment, result: state.result, home:state.home ? {...state.home} : null, settlementSkip:state.lastSettlementSkip ? {...state.lastSettlementSkip} : null,
        damage: state.damage.slice(-12),fieldSettlements:fieldSettlements.map(r=>({...r})),
        heartbeat: { ...heartbeat, retryInMs: Math.max(0, heartbeat.retryAt - Date.now()) },
        trial: combat?.trial || null, mainline: questRows(),
        flight: flight ? { active: flight.active, phase: flight.phase, altitude: flight.altitude,
          starting: !!flightRuntime?.info?.starting, mana: flightRuntime?.info?.manaCurrent ?? null } : null,
        move: state.trace[state.trace.length - 1] || null, navigation: state.navigation, recent: state.log.slice(-40) };
    },
    // 仅交接本助手已捕获的同一场景，更新后继续使用真实碰撞与飞行回调。
    handoff() { return { runtime:flightRuntime?.runtime || null, combat:fieldCombatRuntime, quests:questRuntime,
      environment:fieldEnvironment, flightRuntime, questCatalogSource, questCatalog, rewardRequests, fieldSettlements,
      moonDiagnostics:{history:moonDiagnosticHistory,context:moonDiagnosticContext} }; },
    inspectRoute(goal, radius = 2) {
      const world = readWorld(), p = world?.actorPosition, env = fieldEnvironment;
      if (!p || !env || !position3(goal)) return { ready:false };
      const probe = point => ({ position:point.slice(), ground:env.groundHeight?.(point[0],point[2],point[1]),
        groundFromZero:env.groundHeight?.(point[0],point[2],0), walk:env.canWalk?.(point[0],point[2],.4,point[1]),
        fly:env.canFly?.(point[0],point[2],.4,point[1]), stairs:env.stairGuide?.(point[0],point[2],point[1]) });
      const path = findPath(p, goal, radius), distance = dist(p,goal), length = Math.min(50,distance);
      const samples = [];
      for (let d=0; d<=length; d+=2) samples.push(probe(distance ? [p[0]+(goal[0]-p[0])*d/distance,p[1],p[2]+(goal[2]-p[2])*d/distance] : p));
      return {ready:true, position:p.slice(), path, flight:travelFlightDecision(world,goal,radius,{}),
        recovery:travelFlightDecision(world,goal,radius,{recovery:true}), start:probe(p), samples};
    },
    quality() { applyLowQuality('手动'); return window.__rt?.graphicsQuality?.() || null; },
  };

  // ============ 11. 页面操作面板 ============
  let assistantPanel = null, questList = null, panelStatus = null;
  let lastQuestSignature = '';
  function element(tag, text, parent) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    parent?.append(node); return node;
  }
  function button(parent, label, action) {
    const node = element('button', label, parent); node.type = 'button';
    node.addEventListener('click', async event => {
      event.preventDefault();
      try {
        const result = await action();
        if (typeof result === 'string') panelStatus.textContent = result;
      } catch (error) { reportFailure(error); panelStatus.textContent = error.message; }
    });
    return node;
  }
  const STATUS_LABELS = { available: '可接取', active: '进行中', ready: '待交付', rewarded: '已完成', locked: '未解锁' };
  function renderQuestList() {
    if (!questList) return;
    if(state.panelView==='journal') {
      const rows=journalRows(),signature='journal:'+JSON.stringify(rows);
      if(signature===lastQuestSignature)return;
      lastQuestSignature=signature;questList.replaceChildren();
      if(!rows.length){element('p','进入青溪坊后读取题记目录。',questList);return;}
      for(const row of rows) {
        const article=element('article','',questList);
        element('strong',row.number+' · '+row.name+' · '+(row.found?'已收集':'待收集'),article);
        element('small',(JOURNAL_REGIONS[row.region] || row.region)+' · '+row.area,article);
      }
      return;
    }
    const kind=state.questKind || '主线', rows=questRows(true,kind);
    const signature=kind+JSON.stringify(rows);
    if(signature===lastQuestSignature)return;
    lastQuestSignature=signature;questList.replaceChildren();
    if(!questCatalog){element('p','点击「查看全部任务」读取完整目录和进度。',questList);return;}
    const select=element('select','',questList); select.setAttribute('aria-label','任务分类');
    for(const value of ['全部任务','全部支线',...taskKinds()]) {
      const option=element('option',value,select);option.value=value;option.selected=value===kind;
    }
    select.addEventListener('change',()=>{state.questKind=select.value;lastQuestSignature='';renderQuestList();});
    button(questList,'运行当前分类',()=>runStory({kind:state.questKind || '主线'}));
    const count=status=>rows.filter(q=>q.status===status).length;
    element('p',`${kind}：共 ${rows.length} 项 · 可推进 ${count('available')+count('active')+count('ready')} · 未解锁 ${count('locked')} · 已完成 ${count('rewarded')}`,questList);
    let completed=null;
    for(const row of rows) {
      let parent=questList;
      if(row.status==='rewarded') {
        if(!completed){completed=element('details','',questList);element('summary','已完成任务（'+count('rewarded')+'）',completed);}
        parent=completed;
      }
      const article=element('article','',parent);
      element('strong',`${row.kind} · ${row.title} · ${STATUS_LABELS[row.status] || row.status}`,article);
      if(row.status==='locked')element('p','前置：'+(prerequisiteText(row) || '需满足游戏任务簿中的解锁条件'),article);
      if(row.contact)element('p',`${row.status==='available'?'接取':row.status==='ready'?'交付':'交谈'}：${row.contact.name} · ${row.contact.location || row.region}`,article);
      for(const objective of row.objectives)element('p',`${objective.text} ${objective.progress}/${objective.required}`,article);
      for(const message of row.unsupported)element('small',message,article);
      if(Object.keys(row.timers).length)element('p','限时任务，请在游戏任务簿核对剩余时间。',article);
      if(['available','active','ready'].includes(row.status)) {
        button(article,'地图标记',()=>markQuest(row.id));
        if(row.contact)button(article,'就近交互',()=>interactQuest(row.id));
      }
    }
  }

  function moonDiagnosticText() {
    const labels={run_start:'开始记录',run_replaced:'进入下一局',scene_left:'退出场景并留档',sample:'状态采样',
      rune_round_start:'开始本轮镇纹',mode_change:'原生阶段切换',rune_target:'选择阵纹路线',rune_walk:'阵纹移动决策',
      rune_walk_exit:'阵纹寻路结束',rune_walk_error:'阵纹寻路异常',rune_arrival:'到达后的距离检查',
      rune_interact:'普通交互前后快照',rune_unconfirmed:'交互后未确认封印',rune_state:'原生阵纹状态变化',
      rune_timeout:'原生镇纹超时',runes_complete:'原生确认三纹已封',rune_targets_missing:'镇纹交互目标暂缺',
      damage:'角色受伤',boss_heal:'首领回血',submit:'原生结算上报',receipt:'原生结算返回',settlement:'副本结算留档'};
    const lines=[moonDiagnosticSummary(),'最近三局本地留档；缺失原生计数标为未知。镇纹时间是开始观察后的经过时间，不冒充原生倒计时。'];
    for (const run of moonDiagnosticHistory.slice().reverse()) {
      lines.push('\n===== '+new Date(run.startedAt).toLocaleString()+' · '+run.version+' · '+run.id+' =====',
        '日志 '+run.events.length+' 条；环形淘汰 '+(run.dropped||0)+' 条；'+(run.endReason||'尚未记录退出'));
      for (const event of run.events) {
        const s=event.sample||{},counts=s.counts,number=value=>value==null?'未知':value;
        lines.push(new Date(event.at).toLocaleTimeString()+' ['+(labels[event.type]||event.type)+'] 战斗 '+number(s.seconds)+' 秒 / '+(s.mode||'未知')+
          ' / 阵纹 '+(s.runes?.join(',')||'—')+' / 超时 '+number(counts?.runeTimeouts));
        lines.push(JSON.stringify(event));
      }
    }
    return lines.join('\n');
  }
  function showMoonDiagnostics() {
    observeMoonDiagnostics();
    document.getElementById('dwellingMoonDiagnostics')?.remove();
    const box=element('div','',document.body);box.id='dwellingMoonDiagnostics';
    box.setAttribute('role','region');box.setAttribute('aria-label','月殿详细日志');
    box.style.cssText='position:fixed;right:24px;top:7vh;width:min(780px,calc(100vw - 48px));max-height:86vh;overflow:auto;z-index:10001;padding:16px;box-sizing:border-box;background:#18261ff7;color:#f1e7d2;border:1px solid #9d885d;border-radius:8px;font:13px/1.6 system-ui';
    element('strong','月殿详细日志（保留最近三局，返回洞府后仍可查看）',box);
    const summary=element('p',moonDiagnosticSummary(),box),tools=element('div','',box);
    const text=element('textarea','',box);text.readOnly=true;text.rows=21;text.setAttribute('aria-label','月殿逐步诊断记录');
    text.style.cssText='display:block;width:100%;box-sizing:border-box;margin-top:10px;color:#f1e7d2;background:#101a15;font:12px/1.5 monospace;white-space:pre-wrap';
    const refresh=()=>{observeMoonDiagnostics();summary.textContent=moonDiagnosticSummary();text.value=moonDiagnosticText();};
    button(tools,'刷新日志',refresh);
    button(tools,'导出 JSON',()=>{
      const blob=new Blob([JSON.stringify(moonDiagnostics(),null,2)],{type:'application/json'}),url=URL.createObjectURL(blob);
      const link=element('a','',box);link.href=url;link.download='moon-diagnostics-'+Date.now()+'.json';link.click();link.remove();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    button(tools,'关闭日志',()=>box.remove());
    box.addEventListener('keydown',event=>event.stopPropagation());box.addEventListener('keyup',event=>event.stopPropagation());
    box.addEventListener('pointerdown',releaseKeys);refresh();
  }

  function mountPanel() {
    if (!document.body || document.getElementById('dwellingAutobot')) return;
    const style = element('style', '', document.head);
    style.textContent = '#dwellingAutobot{position:fixed;left:12px;bottom:12px;z-index:10000;width:310px;max-width:calc(100vw - 24px);color:#f1e7d2;background:#18261ff2;border:1px solid #9d885d;border-radius:8px;font:13px/1.6 system-ui;box-shadow:0 3px 18px #0005}#dwellingAutobot summary{cursor:pointer;padding:8px 12px}#dwellingAutobot .body{padding:0 12px 10px;max-height:65vh;overflow:auto}#dwellingAutobot button{margin:4px 5px 4px 0;padding:5px 9px;color:inherit;background:#304538;border:1px solid #887856;border-radius:4px;cursor:pointer}#dwellingAutobot article{border-top:1px solid #52604f;margin-top:10px;padding-top:8px}#dwellingAutobot p{margin:5px 0}#dwellingAutobot small{display:block;color:#d8c89e}';
    assistantPanel = element('details', '', document.body); assistantPanel.id = 'dwellingAutobot';
    element('summary', '洞府助手 2.0.78 · 主支线 / 每日 / 题记', assistantPanel);
    const body = element('div', '', assistantPanel); body.className = 'body';
    const controls = element('div', '', body);
    button(controls, '返回洞府', () => window.__autobot.returnCave());
    button(controls, '进入青溪坊', () => window.__autobot.enter());
    button(controls, '全自动主线', () => runStory());
    button(controls, '查看主线', () => {state.panelView='quests';return mainline();});
    button(controls, '一键全部支线', () => runStory({kind:'全部支线'}));
    button(controls, '御风车任务', () => runStory({kind:'御风车支线'}));
    button(controls, '查看全部任务', () => mainline('全部任务'));
    button(controls, '查看支线', () => {state.panelView='quests';return mainline('全部支线');});
    button(controls, '一键题记收集', () => collectJournal());
    button(controls, '查看题记', inspectJournal);
    button(controls, '停止', () => window.__autobot.stop());
    const trials=element('details','',controls);element('summary','每日与试炼',trials);
    button(trials, '一键每日副本', completeDaily);
    button(trials, '检查每日副本领奖', checkDailyRewards);
    button(trials, '月殿演练（单场）', () => drillDaily('moonpalace')).title = '只打单场；当天机会已用时为无奖演练，仍有奖励机会时按有奖局结算。';
    button(trials, '月殿换装自检', loadoutCheck);
    button(trials, '月殿详细日志', showMoonDiagnostics);
    button(trials, '一键有奖试炼（一重）', completeTrials);
    button(trials, '麦田试炼', () => startTrial('longwind'));
    button(trials, '竹林试炼', () => startTrial('bamboo'));
    button(trials, '接管试炼', () => window.__autobot.resume());
    button(trials, '连续重试至通关', () => window.__autobot.untilVictory());
    panelStatus = element('p', state.phase, body); panelStatus.setAttribute('role', 'status');
    const batchStatus = element('small', '', body);
    const loadoutStatus = element('small', '', body);
    const journalStatus = element('small', '', body);
    const navigationStatus = element('small', '', body);
    const rewardLogDisplay = element('small', '', body);
    const combatStatus = element('small', '', body), moonStatus = element('small', '', body), heartbeatStatus = element('small', '', body);
    const help=element('details','',body);element('summary','使用说明',help);
    element('small', '控制台洞府入口自动进入青溪坊。题记按越国→九国盟→天道盟目录顺序收集，跳过已记入项，未确认或入口未开放即暂停；地面无法起降的点（青黛回环）飞到上空悬停题记，空中不被接受时改沿谷底步行，到不了的点跳过并在结果里列出。所有支线分类自动收录，支持接交、采集、钓鱼、战斗及御风车追逐试驾；每日跳过已领奖/未解锁副本，月殿当天有奖机会已用时不重复挑战；月殿默认保留当前角色装备，按技能栏顺序轮换可用输出法宝，再用术法和点杀补伤，依据实际灵力、冷却和安全窗口出手，不设置累计命中预算；「月殿换装自检」只在手动点击时收宝再祭回、不进副本；一键每日遇到开局即为演练的副本立即退出，结束后复查领奖；「月殿演练（单场）」手动运行一场，机会已用仍可无奖演练；若本局有奖则正常结算；检查领奖按钮只查询不挑战；有奖试炼每局固定一重，入场确认有奖才战斗，无奖立即退出；次数无法校验时只尝试入场查看本局提示，不盲打练习局。副本/试炼结算异常跳过并继续下一场，本次不重试异常场地；全部完成或其他任务出错后返回洞府，异常推送日志群；停止按钮立即取消流程并松键。', help);
    questList = element('div', '', body); renderQuestList();
    // 点面板时不把键盘事件传给游戏；原生按钮的键盘激活仍然可用。
    assistantPanel.addEventListener('keydown', event => event.stopPropagation());
    assistantPanel.addEventListener('keyup', event => event.stopPropagation());
    assistantPanel.addEventListener('pointerdown', releaseKeys);
    let previousPhase = state.phase;
    setInterval(() => {
      if (state.phase !== previousPhase) { previousPhase = state.phase; panelStatus.textContent = state.phase; }
      journalStatus.textContent=state.journal?`题记：${({running:'进行中',complete:'全部收集',limited:'本轮完成',error:'异常停止',cancelled:'已停止'})[state.journal.status]} · 本次 ${state.journal.completed.length} 处 · 累计 ${state.journal.found}/${state.journal.total}${state.journal.current?' · 当前 '+state.journal.current.number+' '+state.journal.current.name:''}`:'';
      batchStatus.textContent = state.batch ? `批量：${({running:'进行中',complete:'已完成',error:'异常停止',cancelled:'已取消'})[state.batch.status]} · 已完成 ${state.batch.completed.length} 场 · 跳过 ${state.batch.skipped.length} 场${state.batch.current?' · 当前 '+(DAILY.find(r=>r.id===state.batch.current)?.name || TRIALS[state.batch.current] || state.batch.current):''}${state.batch.quotas?' · 麦田 '+(state.batch.quotas.longwind ?? '?')+' / 竹林 '+(state.batch.quotas.bamboo ?? '?')+' 次':state.batch.remaining!=null?' · 有奖剩余 '+state.batch.remaining+' 次':''}` : '';
      loadoutStatus.textContent = state.loadout ? '换装：「'+state.loadout.items.map(item=>item.name).join('」「')+'」已收起'+(state.loadout.hold?'，月殿结束后祭回':'，待祭回'+(state.loadout.error?'（'+state.loadout.error+'，自动重试）':'')) : '';
      navigationStatus.textContent = state.story && state.navigation ?
        `移动：${({race:'御风车竞速',flight:'御空',walk:'步行',settling:'等待停稳',idle:'已停稳',waiting:'等待刷新',combat:'反击避敌',casting:'站定施法',syncing:'等待同步'})[state.navigation.mode] || '步行'} · 目标 ${Math.round(state.navigation.distance)} 米${state.navigation.flight ? ' · '+state.navigation.flight.reason : state.navigation.reason ? ' · '+state.navigation.reason : ''}` : '';
      rewardLogDisplay.textContent = '通知日志：' + rewardLogStatus.message + (rewardLogStatus.pending ? ' · 待推送 ' + rewardLogStatus.pending + ' 条' : '');
      combatStatus.textContent = `输出：T ${state.stats.ultimates} · 巨剑 ${state.stats.giants} · 术法 ${state.stats.arts} · 法宝 ${state.stats.treasures} · 点杀 ${state.stats.pierces} · 翻滚 ${state.stats.rolls}`;
      moonStatus.textContent=moonDiagnosticSummary();
      heartbeatStatus.textContent = heartbeat.error ? '性能心跳未登录（401），已暂停重复请求，稍后重试' : '';
      if (assistantPanel.open) renderQuestList();
    }, 1000);
  }
  document.addEventListener('visibilitychange', releaseKeys);
  window.addEventListener('blur', releaseKeys);
  window.addEventListener('pagehide', () => window.__autobot.stop());
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountPanel, { once: true });
  else mountPanel();
  if (rewardBridge && !inherited) {
    const generation = state.generation;
    setTimeout(() => { if (state.generation === generation) void autoEnterFromEntry(); }, 0);
  }
  console.log('[自驾] 已装载。支线：__autobot.sidequests()；每日副本：__autobot.daily()；领奖检查：__autobot.checkDailyRewards()；一重有奖试炼：__autobot.trials()；全自动主线：__autobot.mainline()；试炼：__autobot.start("longwind" / "bamboo")；停止：__autobot.stop()');
})();
