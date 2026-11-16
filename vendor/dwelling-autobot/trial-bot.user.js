// ==UserScript==
// @name         洞府助手（主支线 + 每日副本 + 试炼）
// @namespace    dwelling-autobot
// @version      2.0.53
// @description  主支线、每日副本、试炼与题记按序收集；完成回城，真实奖励和异常推送日志群
// @match        https://asc.aiopenai.app/miniapp/xianxia-dwelling*
// @run-at       document-start
// @grant        unsafeWindow
// @grant        GM_xmlhttpRequest
// @connect      *
// ==/UserScript==

(function () {
  'use strict';
  const window = typeof unsafeWindow === 'undefined' ? globalThis.window : unsafeWindow;

  if (window.__autobot?.version === '2.0.53') return;
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
          const obj = Reflect.apply(target, receiver, args);
          if (slot==='environment')fieldEnvironment=obj;
          else window[slot] = obj;
          if (tracked) { tracked.runtime = obj; flightRuntime = tracked; }
          if (slot === '__fc') {
            fieldEnvironment = args[1]?.environment || null; fieldCombatRuntime = obj;
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
      message:text,phase:safeLogText(state.phase,120),version:'2.0.53'};
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
  window.__dwellingRewardTimer=setInterval(()=>{observeBattleFailure();void flushRewardQueue();},2000);

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
    flightMinDistance: 40, // 与面板一致，按人物到目标的距离判断室外长途御空
    flightAltitude: 16,
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
  function moonThreats(s, me, now) {
    if (s.arena !== 'moonpalace' || !state.story) return [];
    // 读取游戏提供的首领状态；只观察飞剑是否出招，不调用调试破镜/镇压接口。
    const view=window.qingxiNgqBoss?.stats?.(), shots=state.story.moonShots ||= {}, out=[];
    const boss=livingEnemies(s)[0], clock=Number.isFinite(s.wave?.time)?s.wave.time*1000:now;
    if (!boss) return out;
    for (const [name, timing] of Object.entries({yinyang:[1150,3.2],white:[450,1.9],blood:[900,2]})) {
      if (!view?.swords?.[name]?.attacking) { delete shots[name]; continue; }
      const shot=shots[name] ||= {at:clock,origin:me.slice(),reaction:{responded:false}};
      const distance=dist(me,shot.origin), age=clock-shot.at;
      if (distance>timing[1]+1.5) continue;
      const away=bearingTo(me,shot.origin)+Math.PI;
      const tangent=bearingTo(me,boss.position)+state.orbitDir*Math.PI/2;
      // 尚在落点内时沿切线离开；避开边界后的回头再次走进落点。
      const heading=distance<.6?tangent:away;
      out.push({e:boss,move:'ngq-'+name,how:'dodge',dist:dist(me,boss.position),want:heading,
        mobile:true,critical:age>=timing[0]-200 && age<=timing[0]+350,reaction:shot.reaction});
    }
    if (view?.channel != null) {
      const channel=shots.channel ||= {reaction:{responded:false}};
      out.push({e:boss,move:'ngq-channel',how:'dodge',dist:dist(me,boss.position),
        want:bearingTo(me,boss.position)+state.orbitDir*Math.PI/2,mobile:true,
        critical:view.channel>=2.65 && view.channel<3.1,reaction:channel.reaction});
    } else delete shots.channel;
    return out;
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
    if (type!==cast.type) {cast.type=type;cast.lockedAt=null;cast.escape=null;}
    const age=view.time-cast.at, peak=cast.type==='peak', beam=cast.type==='beam';
    const lockAt=peak?2.2:beam?0:2.05, lead=peak?.6:beam?0:.3;
    // 原生黑峰移到头顶时转 idle，冰晶发射时 ice 计数递增；用这些信号校正采样延迟。
    const locked=peak&&view.anim==='idle' || !peak&&!beam&&(view.counts?.ice||0)>cast.ice;
    if (cast.lockedAt==null && (locked || age>=lockAt)) {
      cast.lockedAt=locked?view.time:cast.at+lockAt;
      cast.escape=dist(me,cast.aim)<.6?cast.heading:bearingTo(me,cast.aim)+Math.PI;
    }
    if (cast.lockedAt==null) cast.aim=[me[0]+velocity[0]*lead,me[1],me[2]+velocity[2]*lead];
    const distance=dist(me,boss.position), impact=beam?.55:(cast.lockedAt??cast.at+lockAt)-cast.at+(peak?1.3:distance/26);
    const expires=peak?4.6:beam?.9:4.2;
    if (age>expires || view.mode!=='cast' && !view.vfx?.crystals && !view.vfx?.peak) {track.cast=null;return [];}
    const radius=peak?7.2:beam?1.8:4.8;
    const within=beam ? distance<11 && arrowPathDistance(me,boss.position,cast.aim)<1.8 : dist(me,cast.aim)<radius;
    if (beam && !within) return [];
    // 离开预计落点后保持撤离方向直到落地/弹体消失，避免恢复绕圈又走回阴影。
    return [{e:boss,move:'yufeng-'+cast.type,how:'leave',dist:distance,
      want:cast.lockedAt==null?cast.heading:cast.escape,mobile:true,
      critical:within && age>=impact-(peak?.4:.3) && age<=impact+.35,reaction:cast.reaction,
      hazard:{center:cast.aim.slice(),radius,locked:cast.lockedAt!=null}}];
  }
  // 只读原生机制计数；出手成功不等于刀刃接触，更不等于登车已打断。
  function yufengAwaitingInterrupt(target) {
    return target?.role==='yufeng_mu' && target.behavior?.phase===3 &&
      !(Number.isFinite(target.behavior.counts?.interrupts) && target.behavior.counts.interrupts>0);
  }
  function yufengInterruptFrame(s, target, step, boundary, now) {
    if (!state.story || target?.role!=='yufeng_mu') return false;
    const view=target.behavior, me=readWorld()?.actorPosition;
    if (!view || !me || !Number.isFinite(view.time)) return false;
    const interrupts=Number.isFinite(view.counts?.interrupts)?view.counts.interrupts:0;
    let plan=state.story.muMechanic;
    if (!plan || plan.id!==target.id || view.time<plan.time || view.phase<plan.phase || interrupts<plan.interrupts) {
      plan=state.story.muMechanic={id:target.id,time:view.time,phase:view.phase,interrupts,
        attempts:0,lastHeavyAt:-Infinity,status:'等待登车',confirmedAt:null};
    }
    if (interrupts>plan.interrupts) {
      plan.confirmedAt=view.time;plan.status='原生确认打断';
      log('穆上师：原生确认打断登车（'+interrupts+'次）');
    }
    plan.time=view.time;plan.phase=view.phase;plan.interrupts=interrupts;
    if (view.phase!==3) return false;
    const boarding=(view.channelEnd||0)>view.time || ['channel','board','ride'].includes(view.mode);
    const chase=boarding || view.mode==='retreat' || !interrupts && view.mode==='flinch';
    if (interrupts>0 && !chase) return false;
    state.story.muCast=null;
    const d=dist(me,target.position), nav=reason=>{
      plan.status=reason;
      state.navigation={mode:'mu-interrupt',distance:d,reason,updatedAt:Date.now()};
    };
    // Mu 自己的普通近身退避不能挡住重击；真实招式、其他敌人与低血保护仍优先。
    const unsafe=step.threat || step.dodging || s.airborne || s.paused || document.hidden ||
      window.__rt?.mobileHudState?.()?.blocked || view.vfx?.crystals>0 || view.vfx?.peak ||
      (s.treasureFx||[]).includes('trap') || s.hp/Math.max(1,s.hpMax)<.35 ||
      livingEnemies(s).some(e=>e.id!==target.id && dist(me,e.position)<CFG.retreatDistance);
    if (!chase || unsafe || Math.abs(me[1]-target.position[1])>1.4) {
      nav(unsafe?'危险优先避险，暂停爆发':chase?'高度不适合重击，等待落地':'等待原生登车窗口，暂停爆发');
      if (s.paused || document.hidden || s.airborne || window.__rt?.mobileHudState?.()?.blocked) releaseKeys();
      else {steerWalkable(step.want,boundary,step.threat?.hazard);dodgeThreat(s,step,now);}
      return true;
    }
    if (s.target!==target.id) {
      nav('先锁定穆上师，禁止重击其他目标');releaseKeys();
      if (now-state.lastLockAt>450) {state.lastLockAt=now;act('lock');}
      return true;
    }
    // 保留完整重击动画，不以每帧命令重置刀刃；没收到原生打断时才有限频率重试。
    if (s.action?.kind==='attack' && s.action.clip==='heavy') {
      nav('重击动画中，等待原生打断计数');releaseKeys();return true;
    }
    if (d>2.4) {
      nav('沿可行走路径追近，准备重击破盾');
      steerWalkable(bearingTo(me,target.position),boundary);return true;
    }
    releaseKeys();
    if (!boarding || s.casting || s.gcdLeft>0 || s.action || !(s.stamina>=20) || now-plan.lastHeavyAt<1300) {
      nav('等待重击就绪 / 原生命中确认');return true;
    }
    if (combatAction('heavy',null,now)) {
      plan.lastHeavyAt=now;plan.attempts++;
      nav('已出重击，尚未确认打断');state.lastAct='穆上师 · 近身重击';
      log('穆上师：近身重击破盾（等待原生确认）');
    } else nav('原生暂拒重击，退避后重试');
    return true;
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
    if (!state.story || target?.role!=='yufeng_mu' || yufengAwaitingInterrupt(target)) return false;
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
  function threats(s, me, now) {
    const out = [...qiThreats(s, me, now), ...moonThreats(s, me, now), ...yufengThreats(s, me)];
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
    const input = chooseWalkInput(world,target,fieldEnvironment,[],{center:arenaInfo.center,radius:arenaInfo.radius-2});
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
    if (yufengAwaitingInterrupt(near?.e)) return false;
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
    if (!near || s.gcdLeft > 0 || s.casting || yufengAwaitingInterrupt(near.e)) return 'none';
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
      finishDriving('战绩同步异常：' + (s.trial.syncError || s.trial.anomaly), s, true); return;
    }
    if (['victory', 'defeat'].includes(s.state)) {
      releaseKeys();
      state.settlingAt ||= now;
      state.phase = '等待战绩结算';
      if (s.trial && (s.trial.syncing > 0 || !s.trial.run)) {
        if (now - state.settlingAt > CFG.settlementTimeoutMs) finishDriving('战绩结算超时，请在游戏内核对', s, true);
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

    // 清场后的空档是换波等待：原地松键，不绕场心，也不放技能。
    if (s.wave.interlude > 0 || !(s.enemies || []).some(e => !e.hidden && e.hp > 0)) {
      releaseKeys();
      state.evadeUntil = 0;
      state.gengCancelUntil = 0;
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
  async function workflow(work, {preserveResult=false} = {}) {
    if (state.busy || state.running) return '已有操作进行中，请先停止';
    const owner = ++state.generation;
    state.busy = true; state.error = ''; if (!preserveResult) state.result = null; state.navigation = null;
    try { return await work(owner); }
    catch (error) {
      if (error instanceof Cancelled || owner !== state.generation) return '已停止';
      reportFailure(error);
      state.error = error.message; step(error.message); return error.message;
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
  function finishDriving(message, combat, error = false) {
    if(error)reportFailure(message,combat || readCombat());
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
      grade: combat.trial?.grade || null, rewards: combat.trial?.rewards || [],
      syncing: combat.trial?.syncing || 0, syncError: combat.trial?.syncError || '',
      anomaly: combat.trial?.anomaly || '', verified: !error && combat.state === 'victory' && !!combat.trial?.run && !combat.trial?.syncing,
    };
    step(message);
    if (!error && combat?.state==='victory' && !state.story && fieldEnvironment && window.__quests) {
      const owner=state.generation;
      setTimeout(()=>{ if (owner===state.generation && !state.busy && !state.running) returnTown(); },0);
    }
  }
  function startDriving(arena) {
    releaseKeys(); state.navigation=null;
    arenaInfo = null; state.evadeUntil = 0; state.gengCancelUntil = 0;
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
  async function beginTrial(arena, owner) {
    await prepareTown(owner);
    checkOwner(owner);
    const combat = readCombat();
    if (combat?.active && !combat.overworld && combat.state === 'fighting') {
      if (combat.arena !== arena) throw new Error('另一场试炼正在进行，请先在游戏内结束');
      startDriving(arena); return '已接管当前试炼';
    }
    if (combat?.trial?.syncing > 0) throw new Error('上一场战绩仍在同步，请稍后再开始');
    if (combat?.active && !combat.overworld && combat.arena===arena && combat.state==='defeat') {
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
    applyLowQuality('开战'); startDriving(arena);
    return '已开始';
  }
  async function startTrial(arena = 'longwind') {
    if (!Object.hasOwn(TRIALS, arena)) return '仅支持 longwind（麦田）或 bamboo（竹林）';
    return workflow(async owner => {
      state.retry.enabled = false;
      return beginTrial(arena, owner);
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
    if (state.batch?.status !== 'running' && readCombat()?.state === 'defeat') throw new Error('角色战败，请在游戏内恢复后继续主线');
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
    if (combat?.active && !combat.overworld && combat.state === 'fighting') {
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
      const clearance=hazard?.locked?dist(point,hazard.center):0;
      // 在场边/石块旁选择实际可走的出圈方向，不能因理想方向受阻反而走进落点。
      const score = dist(point, target)+(hazard?.locked?8*Math.max(0,hazard.radius-clearance):0);
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
    for (const distance of [Math.max(8, radius + 2), 16, 24, 36]) {
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
  }
  async function startStoryFlight(owner) {
    releaseKeys(); window.__rt.setFlightVerticalInput?.(0);
    const story = state.story, tracked = flightRuntime;
    const request = { cancelled: false, pitch: readWorld()?.pitch || 0 };
    story.flightRequest = request; story.flying = true;
    if (tracked?.runtime === story.runtime) tracked.request = request;
    if (!readWorld()?.flight?.active && !story.runtime.toggleFlight?.()) {
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
      if (score >= Math.hypot(...target.map((v, i) => v - p[i])) - .02 || !flightSegment(p, next)) continue;
      if (!best || score < best.score) best = { keys, vertical, score };
    }
    return best;
  }
  async function travelFly(goal, radius, owner, options) {
    const landing = flightLandingPoint(goal, radius, myPos());
    if (!landing) { state.story.flightLastFailure='目标附近没有可达的室外落点'; log(state.story.flightLastFailure+'，继续步行后重试'); return; }
    try {
      if (!await startStoryFlight(owner)) {
        state.story.flightRetryAt = Date.now() + 60000;
        state.story.flightLastFailure='御空未获确认'; log(state.story.flightLastFailure+'，继续步行后重试'); return;
      }
      state.story.flightLastFailure='';
      log((options.recovery ? '步行受阻，御空脱困' : '室外长距离御空') + ' · 距离 ' + Math.round(dist(myPos(), goal)));
      const target = [landing[0], Math.max(myPos()[1], landing[1] + CFG.flightAltitude), landing[2]];
      const ceiling = target[1] + 24, deadline = Date.now() + Math.max(45000, dist(myPos(), landing) * 250);
      let closest = Infinity, progressedAt = Date.now();
      while (true) {
        await storyReady(owner);
        const world = readWorld(), p = world.actorPosition;
        if (!world.flight?.active || ['landing', 'dive'].includes(world.flight.phase)) { log('御空已结束，落地后继续步行'); break; }
        if (options.interrupt?.()) break;
        // 岛边两米范围仍可能在裂隙或另一侧地面，落地后必须确有到目标的步行路线。
        const ground = dist(p, landing) <= 2 && landingGround(p);
        if (ground && findPath(ground, goal, radius)) break;
        if (Date.now() > deadline || Date.now() - progressedAt > 5000) { log('御空路线受阻，落地后改走步行路线'); break; }
        const mana = flightRuntime.info?.manaCurrent;
        if ((Number.isFinite(mana) && mana <= 8 || flightCombatBlocked()) && landingGround(p)) {
          state.story.flightRetryAt = Date.now() + 60000;
          log('御空条件变化，落地后继续'); break;
        }
        const distance = Math.hypot(...target.map((v, i) => v - p[i]));
        if (distance < closest - .5) { closest = distance; progressedAt = Date.now(); }
        const ms = Math.max(20, Math.min(CFG.tickMs, dist(p, landing) / (world.flight.cruiseSpeed || 25) * 300));
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
      await landStoryFlight(owner);
    } finally {
      if (owner === state.generation) cancelStoryFlight(state.story);
    }
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
        if (world.flight?.active) { state.story.flying = true; await landStoryFlight(owner); continue; }
        if (options.interrupt?.()) return;
        if (walkGoalReached(p, goal, radius)) return;
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
            if (!path) throw new Error('找不到可步行路线：' + JSON.stringify(goal) + ' · ' + recovery.reason);
          }
          if (!path.length) return;
        }
        while (path.length > 1 && dist(p, path[0]) < .85) path.shift();
        // 看得见后续路点时直接走，减少在短网格节点间摆动。
        for (let i = Math.min(path.length - 1, 12); i > 0; i--) {
          if (walkSegment(p, path[i], fieldEnvironment, obstacles)) { path.splice(0, i); break; }
        }
        const input = chooseWalkInput(world, path[0], fieldEnvironment, obstacles);
        if (input) { setKeys(input.keys); lastInput = input; } else releaseKeys();
        state.navigation = { mode: 'walk', position: p.slice(), goal: goal.slice(), waypoint: path[0].slice(),
          distance: dist(p, goal), flight:flightStatus, keys: input?.keys.join('') || '', heading: input?.heading ?? null, repairs,
          obstacles: obstacles.map(o => o.slice()), updatedAt: Date.now() };
        await storyWait(CFG.tickMs, owner);
      }
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
    await walkTo(point,Math.min(3,row.radius-2),owner);
  }
  async function collectJournalPoint(row,owner) {
    const found=()=>storyData()?.journal?.points?.find(p=>p.id===row.id)?.found===true;
    if(found())return;
    await approachJournal(row,owner);
    await waitForStoryStill(owner);
    const readyBy=Date.now()+12000;
    while(!found()) {
      await storyReady(owner);
      if(await defendNearby(owner)) {await approachJournal(row,owner);await waitForStoryStill(owner);continue;}
      const journal=window.tiannanJournal, target=journal?.stats?.();
      if(target?.target?.id===row.id && target.target.d<=row.radius-1 && !target.busy)break;
      if(Date.now()>readyBy)throw new Error('题记未能就近选中：'+row.name);
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
        while(true) {
          await storyReady(owner);
          const rows=journalRows(), row=rows.find(p=>!p.found);
          state.journal.total=rows.length;state.journal.found=rows.filter(p=>p.found).length;
          if(!row || state.journal.completed.length>=maxPoints) {
            await finishInTown(owner);storyCheck(owner);
            state.journal.status=row?'limited':'complete';state.journal.current=null;
            const result='题记本次收集 '+state.journal.completed.length+' 处，累计 '+state.journal.found+'/'+rows.length+'；已回青溪坊';
            step(result);return result;
          }
          state.journal.current={id:row.id,name:row.name,number:row.number};state.story.current=row.id;
          step('题记 '+row.number+'/'+rows.length+' · '+JOURNAL_REGIONS[row.region]+' · '+row.name);
          await collectJournalPoint(row,owner);storyCheck(owner);
          state.journal.completed.push(row.id);state.story.completed++;
        }
      } catch(error) {
        if(owner===state.generation) {
          reportFailure(error);state.journal.status='error';state.journal.error=safeLogText(error.message);
          state.journal.found=journalRows().filter(p=>p.found).length;
          releaseKeys();cancelStoryFlight(state.story);
          // 收集时限用尽也要留出回城时间；不再收集后续点。
          state.story.deadline=Math.max(state.story.deadline,Date.now()+15*60000);
          try {await finishInTown(owner);}catch{returnJournalProtection();}
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
    throw new Error('野外气血低于 30% 且无法立即回血，已中止任务并尝试安全回城');
  }
  function steerWalkable(heading, boundary = null, hazard = null) {
    const world = readWorld(), p = world?.actorPosition;
    if (!p || !Number.isFinite(heading)) { releaseKeys(); return; }
    const target = [p[0] + Math.sin(heading) * 3, p[1], p[2] + Math.cos(heading) * 3];
    const input = chooseWalkInput(world, target, fieldEnvironment, [], boundary, hazard);
    if (input) setKeys(input.keys); else releaseKeys();
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
    if (outputEnabled && yufengInterruptFrame(s,target,step,boundary,now)) {
      attack(s,null,now,true,true);return;
    }
    if (outputEnabled && yufengBurst(s,target,step,now)) {
      attack(s,null,now,true,true);
      state.navigation={mode:'casting',distance:d,reason:'安全空档站定施法，预警出现即恢复躲避',updatedAt:Date.now()};
      return;
    }
    if (!step.threat?.hazard && !step.retreat && d > 25) heading = bearingTo(me, target.position);
    else heading = step.want;
    const bounds = (Array.isArray(boundary) ? boundary : [boundary]).filter(Boolean);
    const edge = bounds.find(b => dist(me,b.center) > b.radius - 2);
    if (edge && !step.threat?.hazard) heading = bearingTo(me, edge.center);
    steerWalkable(heading, boundary, step.threat?.hazard);
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
  async function fightEnemy(enemyId, owner) {
    const deadline = Date.now() + 5 * 60000;
    const receiptStart=fieldSettlementSequence,spawnIds=new Set([enemyId]);
    const checkSettlement=()=>{
      const rejected=fieldSettlements.find(r=>r.sequence>receiptStart && spawnIds.has(r.enemyId) && !r.ok && r.error==='quest_enemy_combat_invalid');
      if(!rejected)return;
      releaseKeys();const s=readCombat();
      if(s?.active && s.state==='fighting' && !s.paused)act('pause');
      throw new Error('击杀结算被服务端拒绝：'+rejected.enemyId+' · '+rejected.error+'；已停止自动重打并尝试安全回城');
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
          if (Date.now() > deadline || Date.now()-waitingAt > CFG.settlementTimeoutMs) throw new Error('击杀记录同步超时，已中止任务并尝试安全回城');
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
    if(rejected)throw new Error('击杀结算被服务端拒绝：'+rejected.enemyId+' · '+rejected.error+'；已停止自动重打并尝试安全回城');
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
  async function instanceObjective(q, objective, owner) {
    const id = objective.instance, label = INSTANCE_LABELS[id];
    if (!label) throw new Error('未知主线副本：' + id);
    let s = readCombat();
    if (!s?.active || s.overworld || s.arena !== id) {
      step('主线副本：' + label);
      if (s?.arena !== id || s.overworld) await travelMenu(label, owner);
      await until(() => {
        const live = readCombat();
        if (live?.active && !live.overworld && live.arena === id && live.state === 'fighting') return true;
        return window.__rt.startTownCombat(id) === true;
      }, 420000, '剧情副本开战', owner);
    }
    arenaInfo = null; state.navigation=null;
    state.lastHp = s?.hp ?? null;
    const deadline = Date.now() + 20 * 60000;
    while (true) {
      await storyReady(owner);
      s = readCombat();
      if (!s?.active || s.overworld || s.arena !== id) throw new Error('剧情副本已退出');
      recordDamage(s, performance.now());
      if (Date.now() > deadline) throw new Error('剧情副本超时');
      const result = instanceResult(s, id);
      if (result?.error) throw new Error('剧情副本记录异常：' + result.error);
      if (s.state === 'defeat' && !q) {
        releaseKeys();
        await until(() => !!instanceResult(readCombat(), id)?.result || !!instanceResult(readCombat(), id)?.error, 180000, '副本失败结算', owner);
        storyCheck(owner);
        if (instanceResult(readCombat(), id)?.error) throw new Error('副本失败结算异常');
        const quota = await dailyStatus(id, owner);
        batchDay(quota.day);
        if (quota.claimed) throw new Error('副本失败后奖励状态改变，请核对结算');
        state.batch.history.push({ id, outcome:'defeat', at:Date.now() });
        if (state.batch.history.length>20) state.batch.history.shift();
        state.story.moonShots=null; state.story.mirrorSeen=null; state.story.moonWheelChecked=false;
        step('每日副本重试：' + label);
        if (!act('start')) throw new Error('游戏未接受副本重新挑战');
        await storyWait(300, owner); continue;
      }
      if (s.state === 'victory') {
        releaseKeys();
        await until(() => !!instanceResult(readCombat(), id)?.result || !!instanceResult(readCombat(), id)?.error, 180000, '剧情副本结算', owner);
        storyCheck(owner);
        const final = instanceResult(readCombat(), id);
        if (final?.error) throw new Error('副本结算异常：' + final.error);
        if (!q) {
          if (final?.result?.kind !== 'clear') throw new Error('副本结算未确认通关：' + (final?.result?.kind || '未知'));
          return final.result;
        }
        await window.__quests.refresh(); storyCheck(owner);
        if ((storySlot(q.id)?.progress?.[objective.id] || 0) < (storySlot(q.id)?.required?.[objective.id] ?? objective.required)) {
          throw new Error('副本已结束，但服务端尚未确认主线进度，请核对结算');
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
      if (combat?.active && !combat.overworld && combat.state === 'victory') {
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
          await storyReady(owner);
          if (state.story.completed >= maxTasks) {
            await finishInTown(owner); step(kind + '本次已完成 ' + state.story.completed + ' 项；已回青溪坊'); return kind+'本次任务已完成';
          }
          const row = nextStoryQuest(maxChapter);
          if (!row) {
            const unfinished = questRows(true).filter(q => q.chapter <= maxChapter && q.status !== 'rewarded');
            if (kind !== '主线') {
              await finishInTown(owner);
              const locked = unfinished.filter(q=>q.status==='locked').length, deferred=state.story.deferred;
              const deferredText=deferred.length ? '；暂缓 '+deferred.length+' 项：'+deferred.map(q=>q.title+'（'+q.reason+'）').join('、') : '';
              step(kind+'已完成 ' + state.story.completed + ' 项' + (locked ? '，另有 ' + locked + ' 项需先完成前置任务' : '') + deferredText + '；已回青溪坊');
              return deferred.length ? '本次可推进任务已完成，暂缓 '+deferred.length+' 项' : '当前已解锁'+kind+'已完成';
            }
            if (unfinished.length) throw new Error('主线暂无可推进任务，仍有未完成或需手动处理任务：' + unfinished.slice(0, 3).map(q => q.title).join('、'));
            await finishInTown(owner);
            step((Number.isFinite(maxChapter) ? '主线已全部完成（至第' + maxChapter + '章）' : '主线已全部完成')+'；已回青溪坊'); return '主线已完成';
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
          try { await finishInTown(owner); }
          catch { /* 未结算副本保持暂停保护，保留原异常供核查。 */ }
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
    {id:'mulanshrine',name:'血祭神坛',key:'shrine'},
    {id:'bqpuppet',name:'试傀场',key:'puppet'},
    {id:'jianzhong',name:'剑冢',key:'jianzhong'},
  ];
  async function rewardStatus(row, owner) {
    checkOwner(owner);
    const driver = (window.qingxiCombatContent || []).flatMap(pack => Object.entries(pack.arenas || {})).find(([id]) => id === row.id)?.[1]?.driver;
    const request = rewardRequests[row.key] || window[row.global] || driver?.request?.bind(driver);
    if (typeof request !== 'function') throw new Error('未找到奖励查询入口：' + row.name);
    let timer;
    try {
      const data = await Promise.race([Promise.resolve().then(() => request('status',row.arena?{arena:row.arena}:{})), new Promise((_,reject) => {
        timer = setTimeout(() => reject(new Error('奖励查询超时：' + row.name)), 30000);
      })]);
      checkOwner(owner);
      if (data?.error || data?.ok === false) throw new Error('奖励查询失败：' + (data.error || row.name));
      const status = data?.[row.key === 'trial' ? 'status' : row.key];
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
  async function trialStatus(owner, arena = 'longwind') {
    const status = await rewardStatus({id:'trial',key:'trial',name:TRIALS[arena],global:'worldTrialRequest',arena}, owner);
    if (status.arena && status.arena!==arena) throw new Error('试炼查询返回另一场地');
    if (![status.remaining,status.used,status.limit].every(n => Number.isInteger(n) && n >= 0) || status.remaining + status.used !== status.limit) {
      throw new Error('试炼有奖次数不完整');
    }
    return status;
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
          if (previous.trial?.syncing || instanceResult(previous,previous.arena)?.settling) throw new Error('上一场战绩仍在结算');
          if (mode==='trials' && Object.hasOwn(TRIALS,previous.arena) && previous.paused && previous.state==='fighting') act('pause');
          if (previous.state !== 'fighting' && !(previous.state==='defeat' && (mode==='daily' && DAILY.some(r=>r.id===previous.arena) || mode==='trials' && Object.hasOwn(TRIALS,previous.arena)))) act('stop');
        }
        await prepareTown(owner);
        await until(() => !!window.__quests,30000,'任务控制器',owner);
        if (!fieldEnvironment) throw new Error('未捕获道路碰撞数据，请启用脚本后重新进入坊市');
        rt = window.__rt; camera = rt.cameraFollowSettings?.()?.enabled;
        state.story = {runtime:rt,quests:window.__quests,options:{},deadline:Date.now()+180*60000,current:null,completed:0,camera};
        rt.setCameraFollowEnabled?.(false);
        window.__quests.close?.(); clickOnce('worldSheetClose'); canvas()?.focus?.({preventScroll:true});
        const result = await work(owner);
        await finishInTown(owner);
        storyCheck(owner); state.batch.status='complete'; state.batch.current=null;
        step(result+'；已回青溪坊'); return result;
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
  async function finishInTown(owner) {
    storyCheck(owner); releaseKeys();
    if (townIsSafe()) return;
    const combat=readCombat();
    if (window.__fc?.isGhost) throw new Error('灵魂状态，请先正常还魂再回城');
    if (combat?.active && !combat.overworld) {
      if (combat.state!=='victory' || combat.trial?.syncing || instanceResult(combat,combat.arena)?.settling) throw new Error('当前副本未完成结算，已暂停保护，请先核对战绩');
      if (!act('stop')) throw new Error('游戏未接受结算后退出副本');
    }
    const live=readCombat();
    if (live?.active && live.state==='fighting' && !live.paused && !act('pause')) throw new Error('无法暂停野外战斗保护角色');
    step('返回青溪坊安全区');
    await prepareBatchPortal(owner,true);
    await until(townIsSafe,30000,'青溪坊安全区',owner);
    closeWorldControls();
    state.home={verified:true,position:myPos()?.slice(),at:Date.now()};
  }
  async function returnTown() {
    return workflow(async owner=>{
      const rt=window.__rt, camera=rt?.cameraFollowSettings?.()?.enabled;
      state.story={runtime:rt,quests:window.__quests,options:{},deadline:Date.now()+15*60000,current:null,completed:0,camera};
      try {
        await finishInTown(owner); step('已回青溪坊安全区'); return '已回青溪坊安全区';
      } catch (error) {
        const combat=readCombat();
        if (combat?.active && combat.state==='fighting' && !combat.paused) act('pause');
        throw error;
      } finally {
        if (owner===state.generation) {releaseKeys();cancelStoryFlight(state.story);state.story=null;}
      }
    },{preserveResult:true});
  }
  async function completeDaily() {
    return batchWorkflow('daily',async owner => {
      for (const row of DAILY) {
        await storyReady(owner);
        state.batch.current=row.id;
        step('核对今日奖励：' + row.name);
        const status=await dailyStatus(row.id,owner); storyCheck(owner); batchDay(status.day);
        if (status.claimed || !status.hasKey) {
          state.batch.skipped.push({id:row.id,name:row.name,reason:status.claimed?'已领奖':'未解锁'}); continue;
        }
        // 导航可能较长，到入口后再核对，以免跨日或用户在另一窗口领取。
        const live=readCombat();
        if (live?.active && !live.overworld && live.state==='fighting' && live.arena!==row.id) throw new Error('请先结束当前副本，再按顺序开始每日副本');
        if (!live?.active || live.overworld || live.arena!==row.id) { await prepareBatchPortal(owner); await travelMenu(INSTANCE_LABELS[row.id],owner); }
        const before=await dailyStatus(row.id,owner); storyCheck(owner); batchDay(before.day);
        if (before.claimed || !before.hasKey) {
          state.batch.skipped.push({id:row.id,name:row.name,reason:before.claimed?'已领奖':'未解锁'}); continue;
        }
        await instanceObjective(null,{instance:row.id},owner);
        const deadline=Date.now()+60000;
        while (true) {
          const after=await dailyStatus(row.id,owner); storyCheck(owner); batchDay(after.day);
          if (after.claimed) break;
          if (Date.now()>deadline) throw new Error(row.name+'通关后未确认领奖');
          await storyWait(1000,owner);
        }
        state.batch.completed.push({id:row.id,name:row.name,day:state.batch.day});
        state.story.completed++;
        if (!act('stop')) throw new Error('游戏未接受退出副本');
      }
      return '今日每日副本已处理：完成 '+state.batch.completed.length+' 场，跳过 '+state.batch.skipped.length+' 场';
    });
  }
  function ownedTrial(quota) {
    const live=readCombat(), run=quota.activeRun;
    return run?.id && live?.active && !live.overworld && ['fighting','defeat'].includes(live.state) &&
      Object.hasOwn(TRIALS,live.arena) && run.arena===live.arena && live.trial?.run?.id===run.id && run.rewarded===true ? live : null;
  }
  async function completeTrials() {
    return batchWorkflow('trials',async owner => {
      let index=0;
      while (true) {
        await storyReady(owner);
        const quotas={};
        for (const arena of Object.keys(TRIALS)) { quotas[arena]=await trialStatus(owner,arena); storyCheck(owner); batchDay(quotas[arena].day); }
        state.batch.quotas=Object.fromEntries(Object.entries(quotas).map(([id,q])=>[id,q.remaining]));
        state.batch.remaining=Object.values(quotas).reduce((n,q)=>n+q.remaining,0);
        const existing=Object.values(quotas).map(ownedTrial).find(Boolean);
        if (Object.values(quotas).some(q=>q.activeRun && !ownedTrial(q))) throw new Error('服务端仍有未结算试炼，请先核对当前战绩');
        if (!state.batch.remaining && !existing) return '今日有奖试炼次数已用完：本次通关 '+state.batch.completed.length+' 场，麦田/竹林剩余均为 0 次';
        const order=index%2?['bamboo','longwind']:['longwind','bamboo'];
        const arena=existing?.arena || order.find(id=>quotas[id].remaining>0), quota=quotas[arena]; state.batch.current=arena;
        // 两个场地各有独立次数，start 时扣除；接管已开战的一场时加回本场作核对基准。
        const beforeRemaining=quota.remaining+(existing?1:0);
        if (!existing) { await prepareBatchPortal(owner); await travelMenu(arena==='bamboo'?'竹林试炼':'麦田试炼',owner); }
        const entryQuota=await trialStatus(owner,arena); storyCheck(owner); batchDay(entryQuota.day);
        const entered=ownedTrial(entryQuota);
        if (entryQuota.activeRun && (!entered || entered.arena!==arena)) throw new Error('入场前仍有另一场未结算试炼，请核对战绩');
        state.batch.quotas[arena]=entryQuota.remaining;
        state.batch.remaining=Object.values(state.batch.quotas).reduce((sum,n)=>sum+n,0);
        if (!entryQuota.remaining && !entered) continue;
        await beginTrial(arena,owner); storyCheck(owner);
        state.retry={enabled:true,attempts:1,history:[]};
        const started=readCombat();
        await until(() => !state.running,180*60000,'有奖试炼通关',owner);
        storyCheck(owner);
        const result=state.result;
        if (state.error || !result?.verified || result.outcome!=='victory' || result.rewarded!==true) throw new Error(state.error || '试炼尚未确认有奖通关');
        const after=await trialStatus(owner,arena); storyCheck(owner); batchDay(after.day);
        if (after.remaining>=beforeRemaining || after.activeRun) throw new Error('试炼结算后有奖次数未确认');
        state.batch.completed.push({id:arena,name:TRIALS[arena],day:after.day,runId:result.runId || started?.trial?.run?.id || null,result});
        state.batch.quotas[arena]=after.remaining; state.batch.remaining=Object.values(state.batch.quotas).reduce((sum,n)=>sum+n,0);
        index=arena==='longwind'?1:0;
        if (!act('stop')) throw new Error('游戏未接受退出试炼');
      }
    });
  }
  function ensureMoonWheel() {
    if (!state.story || state.story.moonWheelChecked) return;
    state.story.moonWheelChecked=true;
    if (fieldEnvironment?.swordSkill?.('wheelOn')===false) act('shieldWheel');
  }
  function goldMirror(s, now) {
    const seen=state.story.mirrorSeen ||= {};
    for (const mirror of s.mechanicTargets || []) {
      const old=seen[mirror.id];
      if (!old || old.state!==mirror.state) seen[mirror.id]={state:mirror.state,since:now,observed:!!old};
    }
    return (s.mechanicTargets || []).filter(m => m.state==='gold' && seen[m.id]?.observed && now-seen[m.id].since<1300)
      .sort((a,b) => dist(myPos(),a.position)-dist(myPos(),b.position))[0];
  }
  async function moonMechanicFrame(s, owner) {
    const enemies=livingEnemies(s), me=myPos();
    const runes=(window.__fc.mechanicInteractions?.() || []).filter(t => t.data?.kind==='boss-rune');
    if (runes.length) {
      // 三枚阵纹的普通 E 交互；选择最短完整路线，不伪造镇压结果。
      const point=t => Array.isArray(t.point)?t.point:[t.point.x,t.point.y,t.point.z];
      const routes=items => items.length ? items.flatMap((item,i) => routes(items.filter((_,j)=>j!==i)).map(rest=>[item,...rest])) : [[]];
      const length=route => route.reduce((sum,t,i)=>sum+dist(i?point(route[i-1]):me,point(t)),0);
      const target=routes(runes).sort((a,b)=>length(a)-length(b))[0][0];
      await walkTo(point(target),1.25,owner,{flight:false,interrupt:()=>readCombat()?.state!=='fighting' || !(window.__fc.mechanicInteractions?.() || []).some(t=>t.data?.id===target.data.id)});
      storyCheck(owner);
      if (dist(myPos(),point(target))<1.5 && (window.__fc.mechanicInteractions?.() || []).some(t=>t.data?.id===target.data.id)) act('interact');
      return true;
    }
    if (s.mechanicTargets?.length) {
      const a=captureArena(s,me), boundary={center:a.center,radius:Math.max(5,a.radius-4)};
      const mirror=goldMirror(s,performance.now());
      const target=mirror?{...mirror,hp:1,maxHp:1}:enemies[0];
      if (target) storyBattleFrame(s,target,boundary,false);
      else releaseKeys();
      if (mirror) {
        // 只向已锁定的金光镜点杀，避免范围术法或锁定前的大招误中红镜。
        if (s.target!==mirror.id) {
          if (performance.now()-state.lastLockAt>180) { state.lastLockAt=performance.now(); act('lock'); }
        } else if (!(s.gcdLeft>0) && !s.casting && s.cooldown?.pierce===0 && s.mana>=CFG.pierceMana) {
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
    version: '2.0.53', state,
    returnTown, enter: () => workflow(async owner => { await prepareTown(owner); return '已进入青溪坊'; }),
    start: startTrial, daily: completeDaily, trials: completeTrials, mainline: runStory, story: runStory, inspectMainline: mainline,
    sidequests: options => runStory({...options,kind:'全部支线'}), inspectSidequests: () => mainline('全部支线'),
    yufeng: options => runStory({...options,kind:'御风车支线'}), inspectQuests: (kind='全部任务')=>mainline(kind), taskKinds, rewardLog: () => ({...rewardLogStatus}),
    journal:collectJournal, inspectJournal, journalPoints:journalRows,
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
      startDriving(s.arena); return '已恢复';
    },
    untilVictory() {
      if (state.busy || state.story) return '请先结束当前主线流程';
      const s = readCombat();
      if (!s?.active || s.overworld || !s.wave || !Object.hasOwn(TRIALS,s.arena) || !['fighting','defeat'].includes(s.state)) return '请先进入麦田或竹林试炼';
      if (s.trial?.syncError || s.trial?.anomaly || s.trial?.syncing > 0) return '请先处理战绩同步状态';
      if (state.retry.enabled) return '连续重试已开启';
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
          muMechanic:state.story.muMechanic ? {...state.story.muMechanic,lastHeavyAt:Number.isFinite(state.story.muMechanic.lastHeavyAt)?state.story.muMechanic.lastHeavyAt:null} : null } : null,
        questSummary: state.questSummary ? JSON.parse(JSON.stringify(state.questSummary)) : null,
        batch: state.batch ? JSON.parse(JSON.stringify(state.batch)) : null,
        retry: {...state.retry,history:state.retry.history.slice()},
        stats: { ...state.stats }, last: state.lastAct, arena: arenaInfo, collisionReady:!!fieldEnvironment, result: state.result,
        damage: state.damage.slice(-12),fieldSettlements:fieldSettlements.map(r=>({...r})),
        heartbeat: { ...heartbeat, retryInMs: Math.max(0, heartbeat.retryAt - Date.now()) },
        trial: combat?.trial || null, mainline: questRows(),
        flight: flight ? { active: flight.active, phase: flight.phase, altitude: flight.altitude,
          starting: !!flightRuntime?.info?.starting, mana: flightRuntime?.info?.manaCurrent ?? null } : null,
        move: state.trace[state.trace.length - 1] || null, navigation: state.navigation, recent: state.log.slice(-40) };
    },
    // 仅交接本助手已捕获的同一场景，更新后继续使用真实碰撞与飞行回调。
    handoff() { return { runtime:flightRuntime?.runtime || null, combat:fieldCombatRuntime, quests:questRuntime,
      environment:fieldEnvironment, flightRuntime, questCatalogSource, questCatalog, rewardRequests, fieldSettlements }; },
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

  function mountPanel() {
    if (!document.body || document.getElementById('dwellingAutobot')) return;
    const style = element('style', '', document.head);
    style.textContent = '#dwellingAutobot{position:fixed;left:12px;bottom:12px;z-index:10000;width:310px;max-width:calc(100vw - 24px);color:#f1e7d2;background:#18261ff2;border:1px solid #9d885d;border-radius:8px;font:13px/1.6 system-ui;box-shadow:0 3px 18px #0005}#dwellingAutobot summary{cursor:pointer;padding:8px 12px}#dwellingAutobot .body{padding:0 12px 10px;max-height:65vh;overflow:auto}#dwellingAutobot button{margin:4px 5px 4px 0;padding:5px 9px;color:inherit;background:#304538;border:1px solid #887856;border-radius:4px;cursor:pointer}#dwellingAutobot article{border-top:1px solid #52604f;margin-top:10px;padding-top:8px}#dwellingAutobot p{margin:5px 0}#dwellingAutobot small{display:block;color:#d8c89e}';
    assistantPanel = element('details', '', document.body); assistantPanel.id = 'dwellingAutobot';
    element('summary', '洞府助手 · 主支线 / 每日 / 题记', assistantPanel);
    const body = element('div', '', assistantPanel); body.className = 'body';
    const controls = element('div', '', body);
    button(controls, '回青溪坊', () => window.__autobot.returnTown());
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
    button(trials, '一键有奖试炼', completeTrials);
    button(trials, '麦田试炼', () => startTrial('longwind'));
    button(trials, '竹林试炼', () => startTrial('bamboo'));
    button(trials, '接管试炼', () => window.__autobot.resume());
    button(trials, '连续重试至通关', () => window.__autobot.untilVictory());
    panelStatus = element('p', state.phase, body); panelStatus.setAttribute('role', 'status');
    const batchStatus = element('small', '', body);
    const journalStatus = element('small', '', body);
    const navigationStatus = element('small', '', body);
    const rewardLogDisplay = element('small', '', body);
    const combatStatus = element('small', '', body), heartbeatStatus = element('small', '', body);
    const help=element('details','',body);element('summary','使用说明',help);
    element('small', '控制台洞府入口自动进入青溪坊。题记按越国→九国盟→天道盟目录顺序收集，跳过已记入项，未确认或入口未开放即暂停。所有支线分类自动收录，支持接交、采集、钓鱼、战斗及御风车追逐试驾；每日跳过已领奖/未解锁副本；有奖试炼次数用尽停止。完成后回青溪坊，异常推送日志群；停止按钮立即取消流程并松键。', help);
    questList = element('div', '', body); renderQuestList();
    // 点面板时不把键盘事件传给游戏；原生按钮的键盘激活仍然可用。
    assistantPanel.addEventListener('keydown', event => event.stopPropagation());
    assistantPanel.addEventListener('keyup', event => event.stopPropagation());
    assistantPanel.addEventListener('pointerdown', releaseKeys);
    let previousPhase = state.phase;
    setInterval(() => {
      if (state.phase !== previousPhase) { previousPhase = state.phase; panelStatus.textContent = state.phase; }
      journalStatus.textContent=state.journal?`题记：${({running:'进行中',complete:'全部收集',limited:'本轮完成',error:'异常停止',cancelled:'已停止'})[state.journal.status]} · 本次 ${state.journal.completed.length} 处 · 累计 ${state.journal.found}/${state.journal.total}${state.journal.current?' · 当前 '+state.journal.current.number+' '+state.journal.current.name:''}`:'';
      batchStatus.textContent = state.batch ? `批量：${({running:'进行中',complete:'已完成',error:'异常停止',cancelled:'已取消'})[state.batch.status]} · 已完成 ${state.batch.completed.length} 场 · 跳过 ${state.batch.skipped.length} 场${state.batch.current?' · 当前 '+(DAILY.find(r=>r.id===state.batch.current)?.name || TRIALS[state.batch.current] || state.batch.current):''}${state.batch.quotas?' · 麦田 '+state.batch.quotas.longwind+' / 竹林 '+state.batch.quotas.bamboo+' 次':state.batch.remaining!=null?' · 有奖剩余 '+state.batch.remaining+' 次':''}` : '';
      navigationStatus.textContent = state.story && state.navigation ?
        `移动：${({race:'御风车竞速',flight:'御空',walk:'步行',settling:'等待停稳',idle:'已停稳',waiting:'等待刷新',combat:'反击避敌',casting:'站定施法',syncing:'等待同步'})[state.navigation.mode] || '步行'} · 目标 ${Math.round(state.navigation.distance)} 米${state.navigation.flight ? ' · '+state.navigation.flight.reason : state.navigation.reason ? ' · '+state.navigation.reason : ''}` : '';
      rewardLogDisplay.textContent = '通知日志：' + rewardLogStatus.message + (rewardLogStatus.pending ? ' · 待推送 ' + rewardLogStatus.pending + ' 条' : '');
      combatStatus.textContent = `输出：T ${state.stats.ultimates} · 巨剑 ${state.stats.giants} · 术法 ${state.stats.arts} · 法宝 ${state.stats.treasures} · 点杀 ${state.stats.pierces} · 翻滚 ${state.stats.rolls}`;
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
  console.log('[自驾] 已装载。支线：__autobot.sidequests()；每日副本：__autobot.daily()；有奖试炼：__autobot.trials()；全自动主线：__autobot.mainline()；试炼：__autobot.start("longwind" / "bamboo")；停止：__autobot.stop()');
})();
