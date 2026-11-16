/* Official /staticweb/dwelling-cast-transport.js, captured 2026-11-16.
 * Ordered field-cast delivery. Keep events until the server explicitly acknowledges every event in the batch. */
(function(g){
  function createCastTransport({request,onError=()=>{},sleep=ms=>new Promise(r=>setTimeout(r,ms)),maxPending=2048}={}){
    const pending=[];let sending=null,stopped=false,error=null;
    const transient=e=>!e?.status||e.status===429||e.status>=500;
    function notify(e){error={code:e?.message||'field_cast_unreachable',retryable:transient(e),status:e?.status};onError(error);}
    async function drain(){
      let failures=0;
      while(!stopped&&pending.length){
        const batch=pending.slice(0,12);
        try{
          const result=await request(batch);
          if(result?.ok!==true)throw Object.assign(Error(result?.error||'field_cast_unacknowledged'),{status:result?.status});
          const ack=new Set(result.acked||[]);
          if(!result.off&&!batch.every(e=>ack.has(e.k+':'+e.s)))throw Error('field_cast_unacknowledged');
          pending.splice(0,batch.length);error=null;failures=0;
        }catch(e){
          notify(e);failures++;
          if(!error.retryable||failures>=3)return false;
          await sleep(Math.min(4000,800*2**(failures-1)));
        }
      }
      return !stopped&&!pending.length;
    }
    function flush(){
      if(stopped||error&&!error.retryable)return Promise.resolve(false);
      if(sending)return sending;
      sending=Promise.resolve().then(drain).finally(()=>{sending=null;});return sending;
    }
    return {
      enqueue(event){
        if(stopped||pending.length>=maxPending){notify(Object.assign(Error('field_cast_queue_full'),{status:409}));return false;}
        pending.push(event);void flush();return true;
      },flush,
      retry(){if(!stopped)error=null;return flush();},
      dispose(){stopped=true;},
      get pending(){return pending.length;},get error(){return error;},get sending(){return !!sending;},
    };
  }
  g.createDwellingCastTransport=createCastTransport;
  if(typeof module!=='undefined')module.exports=createCastTransport;
})(typeof window!=='undefined'?window:globalThis);
