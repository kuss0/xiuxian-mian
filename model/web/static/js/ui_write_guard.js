(function(){
  if(typeof window === 'undefined'){
    return;
  }

  var originalPostJson = window.postJson;
  var originalRefreshState = window.refreshState;
  var originalApplySnapshot = window.applySnapshot;
  if(typeof originalPostJson !== 'function' || typeof originalRefreshState !== 'function' || typeof originalApplySnapshot !== 'function'){
    return;
  }

  var guard = {
    writeInFlight: 0,
    lastWriteStartedAt: 0,
    refreshStack: []
  };

  function hasBlockingWrite(startedAt){
    return guard.writeInFlight > 0 || guard.lastWriteStartedAt > Number(startedAt || 0);
  }

  window.postJson = async function(path, payload){
    guard.writeInFlight += 1;
    guard.lastWriteStartedAt = Date.now();
    try{
      return await originalPostJson.apply(this, arguments);
    }finally{
      guard.writeInFlight = Math.max(0, guard.writeInFlight - 1);
    }
  };

  window.refreshState = async function(options){
    var opts = options || {};
    var marker = {
      silent: !!opts.silent,
      startedAt: Date.now()
    };
    if(marker.silent && hasBlockingWrite(marker.startedAt)){
      return false;
    }
    guard.refreshStack.push(marker);
    try{
      return await originalRefreshState.apply(this, arguments);
    }finally{
      guard.refreshStack.pop();
    }
  };

  window.applySnapshot = function(nextSnapshot, options){
    var marker = guard.refreshStack.length ? guard.refreshStack[guard.refreshStack.length - 1] : null;
    if(marker && marker.silent && hasBlockingWrite(marker.startedAt)){
      return false;
    }
    return originalApplySnapshot.apply(this, arguments);
  };
})();
