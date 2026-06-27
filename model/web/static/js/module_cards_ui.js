(function(){
  if(typeof renderModules !== 'function'){
    return;
  }

  function esc(value){
    return typeof escapeHtml === 'function'
      ? escapeHtml(value)
      : String(value == null ? '' : value);
  }

  function toolGroup(html, className){
    if(!html){
      return '';
    }
    return '<div class="module-tools '+className+'">'+html+'</div>';
  }

  var moduleSettingsRegistry = {};

  function settingSection(title, description, controlsHtml){
    if(!controlsHtml){
      return '';
    }
    return ''+
      '<section class="module-setting-section">'+
        '<div class="module-setting-copy">'+
          '<h4>'+esc(title)+'</h4>'+
          '<p>'+esc(description)+'</p>'+
        '</div>'+
        '<div class="module-setting-controls">'+controlsHtml+'</div>'+
      '</section>';
  }

  function settingsGroup(key, title, description, html){
    if(!html){
      return '';
    }
    moduleSettingsRegistry[key] = {
      title: title,
      description: description,
      html: html
    };
    return ''+
      '<div class="module-settings-entry">'+
        '<button type="button" class="btn btn-secondary module-settings-button" data-open-module-settings="'+esc(key)+'" aria-label="打开'+esc(title)+'设置">设置</button>'+
      '</div>';
  }

  function ensureModuleSettingsModal(){
    var modal = document.getElementById('module-settings-modal');
    if(modal){
      return modal;
    }
    modal = document.createElement('div');
    modal.id = 'module-settings-modal';
    modal.className = 'modal-backdrop';
    modal.innerHTML = ''+
      '<div class="modal-card modal-card-wide module-settings-modal-card">'+
        '<div class="modal-header module-settings-modal-header">'+
          '<div>'+
            '<h3 id="module-settings-modal-title" class="modal-title">模块设置</h3>'+
            '<div id="module-settings-modal-desc" class="module-settings-modal-desc"></div>'+
          '</div>'+
          '<button type="button" class="icon-btn" data-close-module-settings aria-label="关闭">×</button>'+
        '</div>'+
        '<div id="module-settings-modal-body" class="module-settings-modal-body"></div>'+
      '</div>';
    document.body.appendChild(modal);
    return modal;
  }

  function closeModuleSettingsModal(){
    var modal = document.getElementById('module-settings-modal');
    if(modal){
      modal.classList.remove('show');
    }
  }

  function renderModuleSettingsModal(){
    var modal = document.getElementById('module-settings-modal');
    if(!modal || !modal.classList.contains('show')){
      return;
    }
    var key = modal.dataset.moduleSettingsKey || '';
    var settings = moduleSettingsRegistry[key];
    if(!settings){
      closeModuleSettingsModal();
      return;
    }
    var titleEl = document.getElementById('module-settings-modal-title');
    var descEl = document.getElementById('module-settings-modal-desc');
    var bodyEl = document.getElementById('module-settings-modal-body');
    if(titleEl){
      titleEl.textContent = settings.title;
    }
    if(descEl){
      descEl.textContent = settings.description || '';
    }
    if(bodyEl){
      bodyEl.innerHTML = settings.html || '';
    }
  }

  function openModuleSettingsModal(key){
    var settings = moduleSettingsRegistry[key];
    if(!settings){
      return;
    }
    var modal = ensureModuleSettingsModal();
    modal.dataset.moduleSettingsKey = key;
    modal.classList.add('show');
    renderModuleSettingsModal();
  }

  function optionHtml(values, selected){
    return (values || []).map(function(choice){
      var value = typeof choice === 'object' ? choice.value : choice;
      var label = typeof choice === 'object' ? choice.label : choice;
      var selectedAttr = String(selected || '') === String(value || '') ? ' selected' : '';
      return '<option value="'+esc(value)+'"'+selectedAttr+'>'+esc(label)+'</option>';
    }).join('');
  }

  renderModules = function(identity){
    var grid = document.getElementById('module-grid');
    if(!grid){
      return;
    }
    if(!identity){
      grid.innerHTML = '';
      return;
    }

    var identityModules = identity.modules || [];
    moduleSettingsRegistry = {};
    var moduleByName = function(name){
      return identityModules.find(function(item){ return item.name === name; }) || null;
    };
    var renderModuleToggle = function(name, label){
      var item = moduleByName(name);
      if(!item){
        return '';
      }
      var enabled = !!item.enabled;
      var switchClass = enabled ? 'switch-on' : 'switch-off';
      var nextEnabled = enabled ? 0 : 1;
      return '<span class="module-subswitch">'+
        '<span class="module-subswitch-label">'+esc(label || name)+'</span>'+
        renderSwitch(switchClass, 'data-toggle-module="1" data-module="'+esc(name)+'" data-enabled="'+nextEnabled+'" aria-label="'+esc((label || name)+'开关')+'"')+
      '</span>';
    };
    var renderMainSwitch = function(moduleName){
      var item = moduleName ? moduleByName(moduleName) : null;
      if(!item){
        return '';
      }
      var enabled = !!item.enabled;
      return '<span class="module-main-switch">'+
        '<span>主</span>'+
        renderSwitch(enabled ? 'switch-on' : 'switch-off', 'data-toggle-module="1" data-module="'+esc(moduleName)+'" data-enabled="'+(enabled ? 0 : 1)+'" aria-label="'+esc(moduleName+'主开关')+'"')+
      '</span>';
    };
    var detailFor = function(name){
      var item = moduleByName(name);
      return item && item.detail ? name+'\n'+item.detail : '';
    };
    var compactDetails = function(names){
      return names.map(detailFor).filter(Boolean).join('\n\n');
    };
    var renderModuleCard = function(title, moduleNote, primaryTools, settingsTools, detail, mainModuleName){
      var summary = getModuleSummaryLineFromDetail(detail);
      var settingsKey = title;
      var settingsButton = settingsGroup(
        settingsKey,
        title+'设置',
        '低频配置已移入弹窗，卡片只保留日常需要扫一眼的状态和开关。',
        settingsTools
      );
      return '<div class="module-card">'+
        '<div class="module-top">'+
          '<div class="module-head-row">'+
            '<div class="module-main">'+
              '<div class="module-title">'+esc(title)+'</div>'+
              (summary ? '<div class="module-summary">'+esc(summary)+'</div>' : '')+
              (moduleNote || '')+
            '</div>'+
            '<div class="module-head-actions">'+renderMainSwitch(mainModuleName)+'</div>'+
          '</div>'+
          toolGroup((primaryTools || '') + settingsButton, 'module-tools-primary')+
        '</div>'+
        '<div class="module-detail">'+renderModuleDetailHtml(detail || '')+'</div>'+
      '</div>';
    };

    var petWarmModule = moduleByName('温养器灵');
    var petTrialModule = moduleByName('器灵试炼');
    var hiddenModules = new Set(['温养器灵','器灵试炼','天机代卜','共历心劫','侍妾远航','极阴祖师','南陇侯','点卯','宗门传功','闯塔','深度闭关','元婴']);

    grid.innerHTML = identityModules.map(function(module){
      if(hiddenModules.has(module.name)){
        return '';
      }
      var moduleNote = '';
      var primaryTools = '';
      var settingsTools = '';

      if(module.name === '法宝'){
        var warmEnabled = !!(petWarmModule && petWarmModule.enabled);
        var trialEnabled = !!(petTrialModule && petTrialModule.enabled);
        moduleNote = '<div class="module-note">抚摸：'+esc(identity.pet_name || '')+'｜温养：'+esc(identity.pet_warm_name || identity.pet_name || '')+'｜试炼：'+esc(identity.pet_trial_name || identity.pet_name || '')+'</div>';
        primaryTools =
          '<span class="module-subswitch"><span class="module-subswitch-label">温养</span>'+
          renderSwitch(warmEnabled ? 'switch-on' : 'switch-off', 'data-toggle-module="1" data-module="温养器灵" data-enabled="'+(warmEnabled ? 0 : 1)+'" aria-label="温养器灵开关"')+
          '</span>'+
          '<span class="module-subswitch"><span class="module-subswitch-label">试炼</span>'+
          renderSwitch(trialEnabled ? 'switch-on' : 'switch-off', 'data-toggle-module="1" data-module="器灵试炼" data-enabled="'+(trialEnabled ? 0 : 1)+'" aria-label="器灵试炼开关"')+
          '</span>'+
          '<button type="button" class="btn btn-secondary module-direct-settings-button" data-open-pet-modal="1">设置</button>';
      }else if(module.name === '野外历练'){
        var dailyNames = ['野外历练','点卯','宗门传功','闯塔','深度闭关','元婴'];
        var checkinWin = identity.checkin_window_local || {};
        var towerWin = identity.tower_window_local || {};
        moduleNote = '<div class="module-note">野外：'+esc(identity.wild_training_strategy || '深入')+
          '｜点卯 '+String(checkinWin.start_hour || 0).padStart(2, '0')+'-'+String(checkinWin.end_hour || 0).padStart(2, '0')+
          '｜闯塔 '+String(towerWin.start_hour || 0).padStart(2, '0')+'-'+String(towerWin.end_hour || 0).padStart(2, '0')+'</div>';
        settingsTools =
          settingSection(
            '日常功能开关',
            '控制日常聚合卡内各自动任务是否启用；关闭子功能只影响该玩法，不会修改其他模块状态。',
            renderModuleToggle('野外历练','野外')+
            renderModuleToggle('点卯','点卯')+
            renderModuleToggle('宗门传功','传功')+
            renderModuleToggle('闯塔','闯塔')+
            renderModuleToggle('深度闭关','闭关')+
            renderModuleToggle('元婴','元婴')
          )+
          settingSection(
            '野外历练策略',
            '控制野外历练发出的路线选择；修改后会随下一轮自动历练生效，不会立即补发额外命令。',
            '<label class="module-setting-field"><span>策略</span><select class="text-input wild-training-select" data-wild-training-strategy="1">'+
            optionHtml(identity.wild_training_strategy_choices || [], identity.wild_training_strategy)+'</select></label>'
          )+
          settingSection(
            '点卯执行窗口',
            '限制自动点卯只在本地时间窗口内执行；用于避开不方便观察的时间段，窗口外不会主动补发。',
            '<button type="button" class="btn btn-secondary" data-open-window-modal="点卯">设置点卯窗口</button>'
          )+
          settingSection(
            '闯塔执行窗口',
            '限制自动闯塔只在本地时间窗口内执行；适合把高频或可能触发后续交互的动作固定到可控时间。',
            '<button type="button" class="btn btn-secondary" data-open-window-modal="闯塔">设置闯塔窗口</button>'
          );
        return renderModuleCard('日常', moduleNote, primaryTools, settingsTools, compactDetails(dailyNames), null);
      }else if(module.name === '侍妾'){
        moduleNote = '<div class="module-note">入梦、天机、心劫、远航按链路独立控制</div>';
        primaryTools =
          renderModuleToggle('天机代卜', '天机')+
          renderModuleToggle('共历心劫', '心劫')+
          renderModuleToggle('侍妾远航', '远航');
        return renderModuleCard('侍妾', moduleNote, primaryTools, '', compactDetails(['侍妾','天机代卜','共历心劫','侍妾远航']), '侍妾');
      }else if(module.name === '玄骨考校'){
        moduleNote = '<div class="module-note">玄骨考校、极阴祖师、南陇侯独立控制</div>';
        settingsTools =
          settingSection(
            '奇遇功能开关',
            '控制玄骨考校、极阴祖师、南陇侯三条奇遇链路是否启用；抉择策略仍在下方单独配置。',
            renderModuleToggle('玄骨考校','玄骨')+
            renderModuleToggle('极阴祖师','极阴')+
            renderModuleToggle('南陇侯','南陇')
          )+
          settingSection(
            '极阴祖师抉择',
            '决定遇到极阴祖师事件时的默认处理。献魂偏收益，收敛偏保守；自动会恢复脚本内置判断。',
            '<button type="button" class="btn btn-secondary" data-jiyin-choice="offer_soul">献魂</button>'+
            '<button type="button" class="btn btn-secondary" data-jiyin-choice="hide_aura">收敛</button>'+
            '<button type="button" class="btn btn-secondary" data-jiyin-choice="auto">自动</button>'
          )+
          settingSection(
            '南陇侯兑换',
            '决定南陇侯相关事件出现时优先兑换的方向；不确定收益时可以选择拒绝，避免拿错消耗型奖励。',
            '<button type="button" class="btn btn-secondary" data-nanlong-choice="exchange_fabao">换法宝</button>'+
            '<button type="button" class="btn btn-secondary" data-nanlong-choice="exchange_gongfa">换功法</button>'+
            '<button type="button" class="btn btn-secondary" data-nanlong-choice="reject">拒绝</button>'
          );
        return renderModuleCard('奇遇', moduleNote, primaryTools, settingsTools, compactDetails(['玄骨考校','极阴祖师','南陇侯']), null);
      }else if(module.name === '观星台'){
        settingsTools =
          settingSection(
            '牵引星种',
            '设置观星台需要牵引时优先选择的星种；留空表示不强行指定，由当前状态或默认策略决定。',
            '<label class="module-setting-field"><span>星种</span><select class="text-input stargazer-select" data-stargazer-star-choice="1">'+
            '<option value="">请选择</option>'+optionHtml(identity.stargazer_star_choices || [], identity.stargazer_star_choice)+'</select></label>'
          )+
          settingSection(
            '星盘同步',
            '主动读取一次观星台状态，用于修正星槽、星种等缓存信息；这是低频校准动作，不应连续点击。',
            '<button type="button" class="btn btn-secondary" data-stargazer-sync="1">同步星盘</button>'
          );
      }else if(module.name === '登天阶'){
        var hasGangfeng = Number(identity.tianti_cycle_count || 0) >= 1;
        primaryTools =
          '<span class="module-subswitch"><span class="module-subswitch-label">问心台</span>'+
          renderSwitch(identity.tianti_wenxin_enabled ? 'switch-on' : 'switch-off', 'data-toggle-tianti-feature="1" data-feature="wenxin" data-enabled="'+(identity.tianti_wenxin_enabled ? 0 : 1)+'" aria-label="问心台开关"')+
          '</span>'+
          (hasGangfeng ? '<span class="module-subswitch"><span class="module-subswitch-label">九天罡风</span>'+
          renderSwitch(identity.tianti_gangfeng_enabled ? 'switch-on' : 'switch-off', 'data-toggle-tianti-feature="1" data-feature="gangfeng" data-enabled="'+(identity.tianti_gangfeng_enabled ? 0 : 1)+'" aria-label="九天罡风开关"')+
          '</span>' : '');
        settingsTools =
          settingSection(
            '挑战档位',
            '设置登天阶自动挑战的目标档位；脚本仍会遵守冷却、安全锁和模块开关，不会因改档立即连续发送。',
            '<label class="module-setting-field"><span>档位</span><select class="text-input tianti-rank-select" data-tianti-rank-choice="1">'+
            '<option value="">请选择</option>'+optionHtml(identity.tianti_rank_choices || [], identity.tianti_rank_choice)+'</select></label>'
          )+
          settingSection(
            '状态同步',
            '主动读取一次天阶状态，用于修正当前档位、轮次和可用子玩法；适合状态明显不准时手动校准。',
            '<button type="button" class="btn btn-secondary" data-tianti-sync="1">同步天阶状态</button>'
          );
      }else if(module.name === '观星'){
        moduleNote = '<div class="module-note">命中全局观星监控后按轮次执行</div>';
      }else if(module.name === '小世界'){
        moduleNote = '<div class="module-note">常用功能留在卡面，阈值与低频动作收进设置</div>';
        primaryTools =
          renderSmallWorldFeature(identity,'manifest','显灵')+
          renderSmallWorldFeature(identity,'harvest','收割')+
          renderSmallWorldFeature(identity,'barrier','护界');
        settingsTools =
          settingSection(
            '低频动作开关',
            '神迹维护、淬炼和刷新属于小世界链路的辅助动作；开启后仍由调度按冷却、安全锁和显灵目标控制节奏。',
            renderSmallWorldFeature(identity,'preach','神迹维护')+
            renderSmallWorldFeature(identity,'refine','淬炼')+
            renderSmallWorldFeature(identity,'refresh','刷新')
          )+
          settingSection(
            '护界禁制策略',
            '库存阈值决定香火低于多少时不再自动开盾；提前分钟决定临灾前多久补盾；最小间隔用于避免过密消耗香火。',
            renderSmallWorldBarrierConfig(identity)
          );
      }else if(module.name === '第二元神'){
        var choices = identity.second_soul_choice_strategy_choices || [{value:'stable',label:'稳固道心'},{value:'break',label:'强行突破'}];
        moduleNote = '<div class="module-note">心魔：'+(identity.second_soul_auto_choice_enabled ? '自动' : '手动')+' / '+esc((identity.second_soul_choice_strategy === 'break') ? '强行突破' : '稳固道心')+'</div>';
        primaryTools = renderSecondSoulChoiceSwitch(identity);
        settingsTools = settingSection(
          '心魔抉择策略',
          '自动抉择开启时，遇到第二元神心魔分支会按这里选择；稳固道心更保守，强行突破更激进。',
          '<label class="module-setting-field"><span>策略</span><select class="text-input second-soul-choice-select" data-second-soul-choice-strategy="1">'+optionHtml(choices, identity.second_soul_choice_strategy)+'</select></label>'
        );
      }else if(module.name === '太一'){
        var nsEnabled = !!identity.taiyi_node_search_enabled;
        primaryTools =
          '<span class="module-subswitch"><span class="module-subswitch-label">搜寻节点</span>'+
          renderSwitch(nsEnabled ? 'switch-on' : 'switch-off', 'data-toggle-taiyi-node-search="1" data-enabled="'+(nsEnabled ? 0 : 1)+'" aria-label="太一搜寻节点开关"')+
          '</span>';
        settingsTools = settingSection(
          '引道元素',
          '设置太一引道优先使用的元素；该配置只影响后续引道选择，实际发送仍会遵守太一模块状态和全局安全控制。',
          '<label class="module-setting-field"><span>元素</span><select class="text-input taiyi-element-select" data-taiyi-element-choice="1">'+optionHtml(identity.taiyi_yindao_choices || [], identity.taiyi_yindao_element)+'</select></label>'
        );
      }

      return renderModuleCard(module.name, moduleNote, primaryTools, settingsTools, module.detail || '', module.name);
    }).join('');
    renderModuleSettingsModal();
  };

  document.addEventListener('click', function(event){
    if(!event.target || !event.target.closest){
      return;
    }
    var openBtn = event.target.closest('[data-open-module-settings]');
    if(openBtn){
      openModuleSettingsModal(openBtn.getAttribute('data-open-module-settings') || '');
      return;
    }
    if(event.target.closest('[data-close-module-settings]') || event.target.id === 'module-settings-modal'){
      closeModuleSettingsModal();
    }
  });

  document.addEventListener('keydown', function(event){
    if(event.key === 'Escape'){
      closeModuleSettingsModal();
    }
  });

  window.setTimeout(function(){
    if(typeof renderAll === 'function'){
      renderAll();
    }
  }, 0);
})();
