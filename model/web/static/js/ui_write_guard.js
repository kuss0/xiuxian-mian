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
    lastUserEditAt: 0,
    unsavedUserEdit: false,
    refreshStack: []
  };
  var EDIT_GRACE_MS = 30000;

  function hasBlockingWrite(startedAt){
    return guard.writeInFlight > 0 || guard.lastWriteStartedAt > Number(startedAt || 0);
  }

  function isEditableElement(element){
    if(!element || !element.closest){
      return false;
    }
    var editable = element.closest('input, textarea, select, [contenteditable="true"]');
    if(!editable){
      return false;
    }
    return !editable.disabled && !editable.readOnly;
  }

  function markUserEdit(event){
    if(!event || !isEditableElement(event.target)){
      return;
    }
    guard.lastUserEditAt = Date.now();
    guard.unsavedUserEdit = true;
  }

  function clearUserEdit(){
    guard.lastUserEditAt = 0;
    guard.unsavedUserEdit = false;
  }

  function hasOpenModal(){
    return !!document.querySelector('.modal-backdrop.show');
  }

  function hasBlockingUserEdit(){
    if(isEditableElement(document.activeElement)){
      return true;
    }
    if(!guard.unsavedUserEdit){
      return false;
    }
    if(hasOpenModal()){
      return true;
    }
    return Date.now() - guard.lastUserEditAt < EDIT_GRACE_MS;
  }

  document.addEventListener('input', markUserEdit, true);
  document.addEventListener('change', markUserEdit, true);

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
    if(marker.silent && (hasBlockingWrite(marker.startedAt) || hasBlockingUserEdit())){
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
    if(marker && marker.silent && (hasBlockingWrite(marker.startedAt) || hasBlockingUserEdit())){
      return false;
    }
    if(!marker || !marker.silent){
      clearUserEdit();
    }
    return originalApplySnapshot.apply(this, arguments);
  };
})();
