(function(){
  function getIdentity(){
    if(typeof getSelectedIdentity === 'function'){
      return getSelectedIdentity();
    }
    return null;
  }

  function esc(value){
    if(typeof escapeHtml === 'function'){
      return escapeHtml(value);
    }
    return String(value == null ? '' : value)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  function optionHtml(values, selected){
    return (values || []).map(function(value){
      var sel = String(value) === String(selected) ? ' selected' : '';
      return '<option value="'+esc(value)+'"'+sel+'>'+esc(value)+'</option>';
    }).join('');
  }

  function clampDailyLimit(value){
    var parsed = parseInt(value, 10);
    if(!Number.isFinite(parsed)){
      return 20;
    }
    return Math.max(1, Math.min(20, parsed));
  }

  function clampBuyBaitCount(value){
    var parsed = parseInt(value, 10);
    if(!Number.isFinite(parsed)){
      return 20;
    }
    return Math.max(1, Math.min(99, parsed));
  }

  function selectedChumSet(fishing){
    var values = Array.isArray(fishing.chum_names) ? fishing.chum_names : [];
    if(!values.length && fishing.chum_name){
      values = [fishing.chum_name];
    }
    var out = {};
    values.forEach(function(value){
      out[String(value)] = true;
    });
    return out;
  }

  function chumCheckboxHtml(fishing){
    var choices = fishing.chum_choices || [];
    var selected = selectedChumSet(fishing);
    if(!choices.length){
      return '<span class="fishing-muted">暂无窝料</span>';
    }
    return choices.map(function(value){
      return '<label class="toggle-field fishing-toggle fishing-chum-choice">'+
        '<input type="checkbox" name="chum_names" value="'+esc(value)+'" '+(selected[String(value)] ? 'checked' : '')+' />'+
        '<span>'+esc(value)+'</span>'+
      '</label>';
    }).join('');
  }

  function findFishingCard(){
    var cards = document.querySelectorAll('.module-card');
    for(var i = 0; i < cards.length; i += 1){
      var title = cards[i].querySelector('.module-title');
      if(title && title.textContent.trim() === '灵溪垂钓'){
        return cards[i];
      }
    }
    return null;
  }

  function requirementHtml(plan){
    var requirements = plan && Array.isArray(plan.requirements) ? plan.requirements : [];
    if(!requirements.length){
      return '<span class="fishing-muted">未读取鱼饵需求</span>';
    }
    return requirements.map(function(item){
      var available = item.available_count == null ? '未知' : item.available_count;
      var missing = Number(item.missing_count || 0);
      var cls = missing > 0 ? ' fishing-need-missing' : '';
      return '<span class="fishing-need'+cls+'">'+esc(item.bait)+' '+esc(available)+'/'+esc(item.required_count)+'</span>';
    }).join('');
  }

  function resourceRequirementHtml(plan){
    var requirements = plan && Array.isArray(plan.resource_requirements) ? plan.resource_requirements : [];
    if(!requirements.length){
      return '<span class="fishing-muted">无额外消耗</span>';
    }
    return requirements.map(function(item){
      var available = item.available_count == null ? '未知' : item.available_count;
      var missing = Number(item.missing_count || 0);
      var cls = missing > 0 ? ' fishing-need-missing' : '';
      return '<span class="fishing-need'+cls+'">'+esc(item.item_name)+' '+esc(available)+'/'+esc(item.required_count)+'</span>';
    }).join('');
  }

  function renderFishingConfigPanel(){
    var identity = getIdentity();
    var card = findFishingCard();
    var panel = document.getElementById('fishing-config-panel');
    if(!identity || !card){
      if(panel && panel.parentNode){
        panel.parentNode.removeChild(panel);
      }
      return;
    }
    if(!panel){
      panel = document.createElement('div');
      panel.id = 'fishing-config-panel';
    }
    if(panel.parentNode !== card){
      card.appendChild(panel);
    }
    var fishing = identity.fishing || {};
    var plan = fishing.plan || {};
    var openAttr = (fishing.auto_chum_enabled || fishing.auto_buy_bait_enabled || fishing.auto_probe_enabled) ? ' open' : '';
    panel.innerHTML =
      '<details class="module-submenu fishing-submenu"'+openAttr+'>'+
      '<summary><span>灵溪垂钓</span><strong>'+esc(plan.allow_start ? '可执行' : (plan.blocked_reason || '待配置'))+'</strong></summary>'+
      '<form id="fishing-config-form" class="fishing-config-grid">'+
      '<label class="field-label"><span>鱼塘</span><select class="text-input" name="pond">'+optionHtml(fishing.pond_choices || [], fishing.pond)+'</select></label>'+
      '<label class="field-label"><span>鱼饵</span><select class="text-input" name="bait">'+optionHtml(fishing.bait_choices || [], fishing.bait)+'</select></label>'+
      '<label class="field-label"><span>每日竿数</span><input class="text-input" type="number" name="daily_limit" min="1" max="20" step="1" value="'+esc(clampDailyLimit(fishing.daily_limit))+'" /></label>'+
      '<label class="field-label"><span>买饵数量</span><input class="text-input" type="number" name="auto_buy_bait_count" min="1" max="99" step="1" value="'+esc(clampBuyBaitCount(fishing.auto_buy_bait_count))+'" /></label>'+
      '<label class="toggle-field fishing-toggle"><input type="checkbox" name="auto_chum_enabled" '+(fishing.auto_chum_enabled ? 'checked' : '')+' /><span>打窝</span></label>'+
      '<div class="fishing-plan fishing-chum-plan"><span>窝料顺序</span><div>'+chumCheckboxHtml(fishing)+'</div></div>'+
      '<label class="toggle-field fishing-toggle"><input type="checkbox" name="auto_buy_bait_enabled" '+(fishing.auto_buy_bait_enabled ? 'checked' : '')+' /><span>缺饵购买</span></label>'+
      '<label class="toggle-field fishing-toggle"><input type="checkbox" name="auto_probe_enabled" '+(fishing.auto_probe_enabled ? 'checked' : '')+' /><span>试饵</span></label>'+
      '<div class="fishing-plan"><span>今日</span><div>'+esc(fishing.daily_count || 0)+'/'+esc(clampDailyLimit(fishing.daily_limit))+'</div></div>'+
      '<div class="fishing-plan"><span>鱼饵</span><div>'+requirementHtml(plan)+'</div></div>'+
      '<div class="fishing-plan"><span>资源</span><div>'+resourceRequirementHtml(plan)+'</div></div>'+
      '<div class="fishing-plan fishing-plan-wide"><span>计划</span><div>'+esc(plan.summary || '未生成')+'</div></div>'+
      '<div class="fishing-actions"><button type="submit" class="btn btn-secondary">保存</button></div>'+
      '</form>'+
      '</details>';
  }

  async function submitFishingConfig(event){
    event.preventDefault();
    var form = event.currentTarget;
    var chumEnabled = !!form.querySelector('input[name="auto_chum_enabled"]').checked;
    var chumNames = [];
    form.querySelectorAll('input[name="chum_names"]:checked').forEach(function(input){
      chumNames.push(input.value);
    });
    var payload = {
      send_as_id: appState && appState.selectedId,
      pond: form.querySelector('select[name="pond"]').value,
      bait: form.querySelector('select[name="bait"]').value,
      daily_limit: clampDailyLimit(form.querySelector('input[name="daily_limit"]').value),
      auto_buy_bait_count: clampBuyBaitCount(form.querySelector('input[name="auto_buy_bait_count"]').value),
      auto_chum_enabled: chumEnabled,
      chum_names: chumEnabled ? chumNames : [],
      chum_name: chumEnabled && chumNames.length ? chumNames[0] : '无',
      auto_buy_bait_enabled: !!form.querySelector('input[name="auto_buy_bait_enabled"]').checked,
      auto_probe_enabled: !!form.querySelector('input[name="auto_probe_enabled"]').checked
    };
    try{
      var data = await postJson('/api/fishing-config', payload);
      updateFlash(data.message || '已更新灵溪垂钓', false);
      applySnapshot(data.snapshot || appState.snapshot, {keepFlash:true});
    }catch(error){
      updateFlash((error && error.message) || '灵溪垂钓更新失败', true);
      renderFishingConfigPanel();
    }
  }

  document.addEventListener('submit', function(event){
    if(event.target && event.target.id === 'fishing-config-form'){
      submitFishingConfig(event);
    }
  });

  document.addEventListener('change', function(event){
    var checkbox = event.target && event.target.closest ? event.target.closest('#fishing-config-form input[name="auto_chum_enabled"]') : null;
    if(!checkbox){
      return;
    }
    var form = checkbox.closest('form');
    if(form){
      form.querySelectorAll('input[name="chum_names"]').forEach(function(input){
        input.disabled = !checkbox.checked;
      });
    }
  });

  if(typeof renderAll === 'function'){
    var originalRenderAll = renderAll;
    renderAll = function(){
      originalRenderAll();
      renderFishingConfigPanel();
    };
  }
  window.setTimeout(renderFishingConfigPanel, 0);
})();
