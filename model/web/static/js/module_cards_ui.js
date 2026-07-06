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
          (description ? '<p>'+esc(description)+'</p>' : '')+
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

  function windowInlineConfig(moduleName, windowData){
    var data = windowData || {};
    return ''+
      '<span class="module-setting-current">当前：'+esc(data.text || '')+'</span>'+
      '<span class="module-inline-window" data-inline-window-config="'+esc(moduleName)+'">'+
        '<label class="module-setting-field"><span>开始</span><input class="text-input module-hour-input" type="number" min="0" max="23" step="1" value="'+esc(data.start_hour || 0)+'" data-inline-window-start="1"></label>'+
        '<label class="module-setting-field"><span>结束</span><input class="text-input module-hour-input" type="number" min="0" max="23" step="1" value="'+esc(data.end_hour || 0)+'" data-inline-window-end="1"></label>'+
        '<button type="button" class="btn btn-secondary" data-save-window-inline="'+esc(moduleName)+'">保存</button>'+
      '</span>';
  }

  function petNameInput(fieldName, label, value){
    return '<label class="module-setting-field module-setting-field-wide"><span>'+esc(label)+'</span><input class="text-input module-name-input" type="text" value="'+esc(value || '')+'" data-pet-inline-name="'+esc(fieldName)+'"></label>';
  }

  function currentChoiceText(label, value){
    return '<span class="module-setting-current">'+esc(label)+'：'+esc(value || '未设置')+'</span>';
  }

  function checkedAttr(value){
    return value ? ' checked' : '';
  }

  function settingCheckbox(key, label, checked){
    return '<label class="checkbox-inline checkbox-inline-small module-setting-checkbox">'+
      '<input type="checkbox" data-tianxing-config="'+esc(key)+'"'+checkedAttr(checked)+' /> '+esc(label)+
    '</label>';
  }

  function wanxinCheckbox(key, label, checked){
    return '<label class="checkbox-inline checkbox-inline-small module-setting-checkbox">'+
      '<input type="checkbox" data-wanxin-config="'+esc(key)+'"'+checkedAttr(checked)+' /> '+esc(label)+
    '</label>';
  }

  function renderExploreRiftRebirthConfig(config){
    config = config || {};
    var modeChoices = config.choice_mode_choices || [
      {value:'safe_first', label:'稳妥优先'},
      {value:'root_first', label:'灵根优先'}
    ];
    var rootChoices = config.root_type_choices || [
      {value:'', label:'不限'},
      {value:'天灵根', label:'天灵根'},
      {value:'异灵根', label:'异灵根'},
      {value:'伪灵根', label:'伪灵根'},
      {value:'废灵根', label:'废灵根'}
    ];
    return ''+
      '<label class="module-setting-field"><span>选择</span><select class="text-input" data-explore-rift-rebirth-config="choice_mode">'+optionHtml(modeChoices, config.choice_mode || 'safe_first')+'</select></label>'+
      '<label class="module-setting-field"><span>灵根</span><select class="text-input" data-explore-rift-rebirth-config="preferred_root_type">'+optionHtml(rootChoices, config.preferred_root_type || '')+'</select></label>'+
      '<label class="module-setting-field module-setting-field-wide"><span>属性</span><input class="text-input module-name-input" placeholder="例：雷、冰" value="'+esc(config.preferred_attrs || '')+'" data-explore-rift-rebirth-config="preferred_attrs"></label>'+
      '<label class="module-setting-field"><span>盲选</span><select class="text-input" data-explore-rift-rebirth-config="blind_index">'+optionHtml(config.blind_index_choices || [1,2,3], config.blind_index || 1)+'</select></label>'+
      '<button type="button" class="btn btn-secondary" data-save-explore-rift-rebirth-config="1">保存</button>';
  }

  function moduleCardPriority(title){
    var normalized = String(title || '').trim();
    if(normalized === '日常'){
      return 0;
    }
    if(['合欢宗','天星宗','阴罗宗','慕兰','慕兰烽烟','婉心封魂'].indexOf(normalized) >= 0){
      return 1;
    }
    if(normalized === '灵溪垂钓'){
      return 2;
    }
    if(normalized === '侍妾'){
      return 3;
    }
    if(normalized === '奇遇'){
      return 4;
    }
    return 5;
  }

  function reorderModuleCards(grid){
    if(!grid){
      return;
    }
    var cards = Array.prototype.slice.call(grid.children || []);
    var originalOrder = new Map();
    cards.forEach(function(card, index){
      originalOrder.set(card, index);
    });
    cards.sort(function(a, b){
      var titleA = a.querySelector('.module-title');
      var titleB = b.querySelector('.module-title');
      var priorityA = moduleCardPriority(titleA ? titleA.textContent : '');
      var priorityB = moduleCardPriority(titleB ? titleB.textContent : '');
      if(priorityA !== priorityB){
        return priorityA - priorityB;
      }
      return (originalOrder.get(a) || 0) - (originalOrder.get(b) || 0);
    });
    cards.forEach(function(card){
      grid.appendChild(card);
    });
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
    var hiddenModules = new Set(['温养器灵','器灵试炼','布下剑阵','天机代卜','共历心劫','侍妾远航','极阴祖师','南陇侯','点卯','宗门传功','闯塔','深度闭关','卜筮问天','斗法','探寻裂缝']);

    grid.innerHTML = identityModules.map(function(module){
      if(hiddenModules.has(module.name)){
        return '';
      }
      var moduleNote = '';
      var primaryTools = '';
      var settingsTools = '';

      if(module.name === '法宝'){
        moduleNote = '<div class="module-note">抚摸：'+esc(identity.pet_name || '')+'｜温养：'+esc(identity.pet_warm_name || identity.pet_name || '')+'｜试炼：'+esc(identity.pet_trial_name || identity.pet_name || '')+'</div>';
        settingsTools =
          settingSection(
            '抚摸法宝',
            '目标：'+(identity.pet_name || '未设置'),
            renderModuleToggle('法宝','开关')+petNameInput('pet_name', '目标', identity.pet_name || '')
          )+
          settingSection(
            '温养器灵',
            '目标：'+(identity.pet_warm_name || identity.pet_name || '未设置'),
            renderModuleToggle('温养器灵','开关')+petNameInput('pet_warm_name', '目标', identity.pet_warm_name || identity.pet_name || '')
          )+
          settingSection(
            '器灵试炼',
            '目标：'+(identity.pet_trial_name || identity.pet_name || '未设置'),
            renderModuleToggle('器灵试炼','开关')+petNameInput('pet_trial_name', '目标', identity.pet_trial_name || identity.pet_name || '')
          )+
          settingSection(
            '布下剑阵',
            '固定发送 .布下剑阵｜下次：'+esc(((identity.timers || {}).next_pet_formation_time) || '未设置'),
            renderModuleToggle('布下剑阵','开关')
          )+
          settingSection(
            '名称保存',
            '修改目标名后保存；留空的温养或试炼目标会沿用抚摸目标。',
            '<button type="button" class="btn btn-secondary" data-save-pet-inline="1">保存名称</button>'
          );
        return renderModuleCard('法宝', moduleNote, primaryTools, settingsTools, compactDetails(['法宝','温养器灵','器灵试炼','布下剑阵']), null);
      }else if(module.name === '野外历练'){
        var dailyNames = ['野外历练','点卯','宗门传功','闯塔','深度闭关','卜筮问天','斗法'];
        var checkinWin = identity.checkin_window_local || {};
        var towerWin = identity.tower_window_local || {};
        moduleNote = '<div class="module-note">野外：'+esc(identity.wild_training_strategy || '谨慎')+
          '｜点卯 '+String(checkinWin.start_hour || 0).padStart(2, '0')+'-'+String(checkinWin.end_hour || 0).padStart(2, '0')+
          '｜闯塔 '+String(towerWin.start_hour || 0).padStart(2, '0')+'-'+String(towerWin.end_hour || 0).padStart(2, '0')+
          '｜问天 '+esc(identity.divination_daily_limit || 6)+'/日'+
          '｜斗法 '+esc(identity.duel_completed_count || 0)+'/'+esc(identity.duel_total_count || 0)+'</div>';
        settingsTools =
          settingSection(
            '野外历练',
            '策略随下一轮历练生效，不会因修改配置立即补发命令。',
            renderModuleToggle('野外历练','开关')+
            '<label class="module-setting-field"><span>策略</span><select class="text-input wild-training-select" data-wild-training-strategy="1">'+
            optionHtml(identity.wild_training_strategy_choices || [], identity.wild_training_strategy)+'</select></label>'
          )+
          settingSection(
            '点卯',
            '只在本地时间窗口内执行，窗口外不主动补发。',
            renderModuleToggle('点卯','开关')+windowInlineConfig('点卯', checkinWin)
          )+
          settingSection(
            '宗门传功',
            '按宗门传功冷却执行，关闭后只影响传功链路。',
            renderModuleToggle('宗门传功','开关')
          )+
          settingSection(
            '闯塔',
            '只在本地时间窗口内执行，适合把可能触发后续交互的动作固定到可观察时段。',
            renderModuleToggle('闯塔','开关')+windowInlineConfig('闯塔', towerWin)
          )+
          settingSection(
            '深度闭关',
            '闭关结算仍按被动回复和安全锁处理。',
            renderModuleToggle('深度闭关','开关')
          )+
          settingSection(
            '卜筮问天',
            '每日次数控制问天查询上限。',
            renderModuleToggle('卜筮问天','开关')+
            '<label class="module-setting-field"><span>次数</span><input class="text-input divination-limit-input" type="number" min="1" max="20" step="1" value="'+esc(identity.divination_daily_limit || 6)+'" data-divination-daily-limit="1"></label>'
          )+
          settingSection(
            '斗法',
            '目标池可填单个或多个目标，多个目标用空格或逗号分隔；批量执行会轮转目标并追加随机错峰。',
            renderModuleToggle('斗法','开关')+
            currentChoiceText('目标池', identity.duel_target || '未配置')+
            currentChoiceText('进度', String(identity.duel_completed_count || 0)+'/'+String(identity.duel_total_count || 0))
          );
        return renderModuleCard('日常', moduleNote, primaryTools, settingsTools, compactDetails(dailyNames), null);
      }else if(module.name === '元婴'){
        var riftModule = moduleByName('探寻裂缝');
        var riftStatus = riftModule ? (riftModule.enabled ? '开' : '关') : '不可用';
        var rebirthConfig = identity.explore_rift_rebirth || {};
        var rebirthModeText = rebirthConfig.choice_mode === 'root_first' ? '灵根优先' : '稳妥优先';
        var rebirthRootText = rebirthConfig.preferred_root_type || '不限';
        moduleNote = '<div class="module-note">元婴：'+esc(identity.yuanying_level_text || '未读取')+'｜裂缝：'+esc(riftStatus)+'</div>';
        settingsTools =
          settingSection(
            '元婴出窍',
            '按冷却和结算推进。',
            renderModuleToggle('元婴','开关')
          )+
          settingSection(
            '探寻裂缝',
            '大凶后会先恢复肉身，再放开其他指令。',
            renderModuleToggle('探寻裂缝','开关')+
            currentChoiceText('夺舍', rebirthModeText+' / '+rebirthRootText)+
            renderExploreRiftRebirthConfig(rebirthConfig)
          );
        return renderModuleCard('元婴', moduleNote, primaryTools, settingsTools, compactDetails(['元婴','探寻裂缝']), null);
      }else if(module.name === '侍妾'){
        moduleNote = '<div class="module-note">入梦、天机、心劫、远航按链路独立控制</div>';
        primaryTools =
          renderModuleToggle('天机代卜', '天机')+
          renderModuleToggle('共历心劫', '心劫')+
          renderModuleToggle('侍妾远航', '远航');
        return renderModuleCard('侍妾', moduleNote, primaryTools, '', compactDetails(['侍妾','天机代卜','共历心劫','侍妾远航']), '侍妾');
      }else if(module.name === '合欢宗'){
        var hehuanRetryMax = Number(identity.hehuan_retry_max_interval_min || 5);
        var hehuanRetryCount = Number(identity.hehuan_retry_count || 0);
        var hehuanRetryLimit = Number(identity.hehuan_retry_limit || 5);
        moduleNote = '<div class="module-note">回复吧唧锚点｜补发随机 1-'+esc(hehuanRetryMax)+' 分钟｜'+esc(hehuanRetryCount)+'/'+esc(hehuanRetryLimit)+'</div>';
        settingsTools = settingSection(
          '自动温养',
          '自动温养会回复10分钟内吧唧发言；没有锚点时先由吧唧发一条锚点。冷却按最近成功+1小时校准，结算卡住或吞回复按随机间隔补发。',
          renderModuleToggle('合欢宗','开关')+
          '<label class="module-setting-field"><span>补发上限</span><input class="text-input module-hour-input" type="number" min="1" max="30" step="1" value="'+esc(hehuanRetryMax)+'" data-hehuan-retry-max-min="1"></label>'+
          currentChoiceText('最近成功', identity.hehuan_last_warm_success_at || '未记录')+
          currentChoiceText('自动调度', identity.hehuan_auto_next_time || '未设置')
        );
      }else if(module.name === '天星宗'){
        var tianxing = identity.tianxing || {};
        var txConfig = tianxing.auto_config || {};
        var txTimeline = tianxing.timeline || {};
        var txRetreatFarm = txTimeline.retreat_farm || {};
        var txCraftFarm = txTimeline.craft_farm || {};
        var txActiveStep = txTimeline.active_step || {};
        var txPhaseText = txTimeline.phase_label || txTimeline.phase || 'idle';
        var txLastErrorText = txTimeline.last_error_label || txTimeline.last_error || '';
        var availableStars = (tianxing.available_stars || []).join('、') || '未记录';
        var txReleasedRoutes = (txTimeline.released_routes || []).map(function(item){
          return String(item.route || '') + (item.released_at ? ' @ '+String(item.released_at || '') : '');
        }).filter(Boolean).join('；') || '无';
        var txAuditText = (txTimeline.audit || []).map(function(item){
          var parts = [item.event || ''];
          if(item.action || item.arg){ parts.push(String(item.action || '') + (item.arg ? ' '+String(item.arg) : '')); }
          if(item.route){ parts.push('路线 '+String(item.route)); }
          return parts.filter(Boolean).join(' / ');
        }).filter(Boolean).join('；') || '无';
        var txActiveStepText = txActiveStep.command || ([txActiveStep.action || '', txActiveStep.arg || ''].join(' ').trim());
        if(!txActiveStepText){ txActiveStepText = '无'; }
        if(txActiveStep.status){ txActiveStepText += ' / '+String(txActiveStep.status); }
        var txPauseText = tianxing.automation_pause_text || (tianxing.automation_paused ? '已暂停' : '未暂停');
        var txFarmWindows = txConfig.farm_windows_text || '02:00-05:00,06:00-11:50,14:30-17:30,23:00-23:35';
        var txTargetTianji = txConfig.target_tianji_daily;
        if(txTargetTianji === undefined || txTargetTianji === null || txTargetTianji === ''){ txTargetTianji = 42; }
        var txAckTimeout = txConfig.ack_timeout_sec;
        if(txAckTimeout === undefined || txAckTimeout === null || txAckTimeout === ''){ txAckTimeout = 120; }
        var txCalibrationBackoff = txConfig.calibration_backoff_sec;
        if(txCalibrationBackoff === undefined || txCalibrationBackoff === null || txCalibrationBackoff === ''){ txCalibrationBackoff = 300; }
        var txMaxReplans = txConfig.max_replans_per_day;
        if(txMaxReplans === undefined || txMaxReplans === null || txMaxReplans === ''){ txMaxReplans = 3; }
        var txCraftLimit = txConfig.craft_farm_daily_limit;
        if(txCraftLimit === undefined || txCraftLimit === null || txCraftLimit === ''){ txCraftLimit = 42; }
        var txCraftIntervalMin = txConfig.craft_farm_interval_min_sec;
        if(txCraftIntervalMin === undefined || txCraftIntervalMin === null || txCraftIntervalMin === ''){ txCraftIntervalMin = 120; }
        var txCraftIntervalMax = txConfig.craft_farm_interval_max_sec;
        if(txCraftIntervalMax === undefined || txCraftIntervalMax === null || txCraftIntervalMax === ''){ txCraftIntervalMax = 300; }
        var txCraftTimeout = txConfig.craft_farm_reply_timeout_sec;
        if(txCraftTimeout === undefined || txCraftTimeout === null || txCraftTimeout === ''){ txCraftTimeout = 120; }
        var txPrepareLead = txConfig.route_prepare_lead_sec;
        if(txPrepareLead === undefined || txPrepareLead === null || txPrepareLead === ''){ txPrepareLead = 300; }
        var txMinTianjiForChange = txConfig.min_tianji_for_change;
        if(txMinTianjiForChange === undefined || txMinTianjiForChange === null || txMinTianjiForChange === ''){ txMinTianjiForChange = 3; }
        var txEstimatedTianji = txCraftFarm.estimated_tianji || tianxing.tianji_value || 0;
        var txShortagePolicy = Number(txEstimatedTianji || 0) < Number(txMinTianjiForChange || 0) ? '缺点先等路线' : '天机够用先等改命';
        moduleNote = '<div class="module-note">命星：'+esc(tianxing.fixed_star || '未定')+
          '｜天机 '+esc(tianxing.tianji_value || 0)+
          '｜改命阈值 '+esc(txMinTianjiForChange)+
          '｜逆命劫 '+esc(tianxing.calamity_count || 0)+
          '｜接管 '+esc(tianxing.automation_paused ? '暂停' : '运行')+
          '｜前置 '+esc(txPhaseText)+
          '｜炼制 '+esc(txCraftFarm.phase || 'idle')+
          '｜试运行 '+(txConfig.strategy_dry_run_enabled ? '开' : '关')+'</div>';
        settingsTools =
          settingSection(
            '状态校准',
            '查盘、观命用于对账；消劫会消耗修为和贡献。',
            settingCheckbox('auto_panel_enabled', '自动查盘', txConfig.auto_panel_enabled)+
            settingCheckbox('auto_observe_enabled', '自动观命', txConfig.auto_observe_enabled)+
            settingCheckbox('daily_observe_enabled', '日切观命', txConfig.daily_observe_enabled)+
            settingCheckbox('auto_clear_calamity_enabled', '自动消劫', txConfig.auto_clear_calamity_enabled)+
            '<label class="module-setting-field"><span>消劫阈值</span><input class="text-input module-hour-input" type="number" min="1" max="99" step="1" value="'+esc(txConfig.min_calamity_to_clear || 1)+'" data-tianxing-config="min_calamity_to_clear"></label>'+
            '<label class="module-setting-field"><span>校准间隔</span><input class="text-input module-hour-input" type="number" min="1" max="24" step="1" value="'+esc(txConfig.status_backoff_hours || 6)+'" data-tianxing-config="status_backoff_hours"></label>'
          )+
          settingSection(
            '自动命令',
            '先试运行，确认不会抢原链路再打开发送。',
            settingCheckbox('auto_set_star_enabled', '自动定命', txConfig.auto_set_star_enabled)+
            settingCheckbox('daily_set_star_enabled', '日切定命', txConfig.daily_set_star_enabled)+
            settingCheckbox('route_special_star_enabled', '特化命星', txConfig.route_special_star_enabled)+
            settingCheckbox('auto_predict_enabled', '自动推命', txConfig.auto_predict_enabled)+
            settingCheckbox('auto_change_fate_enabled', '自动探索改命', txConfig.auto_change_fate_enabled)+
            settingCheckbox('strategy_dry_run_enabled', '命令试运行', txConfig.strategy_dry_run_enabled)+
            '<button type="button" class="btn btn-secondary" data-save-tianxing-config="1">保存设置</button>'
          )+
          settingSection(
            '动作前置',
            '野外、裂缝、炼制前先补必要前置；自动改命仅用于探索兜底，确认后才放行。',
            settingCheckbox('timeline_enabled', '启用时间线', txConfig.timeline_enabled)+
            settingCheckbox('timeline_dry_run_enabled', '时间线试运行', txConfig.timeline_dry_run_enabled)+
            settingCheckbox('allow_prediction_override_enabled', '允许改押推命', txConfig.allow_prediction_override_enabled)+
            settingCheckbox('duel_route_enabled', '斗法前置', txConfig.duel_route_enabled)+
            '<label class="module-setting-field"><span>日目标天机</span><input class="text-input module-hour-input" type="number" min="0" max="999" step="1" value="'+esc(txTargetTianji)+'" data-tianxing-config="target_tianji_daily"></label>'+
            '<label class="module-setting-field"><span>改命阈值</span><input class="text-input module-hour-input" type="number" min="3" max="999" step="1" value="'+esc(txMinTianjiForChange)+'" data-tianxing-config="min_tianji_for_change"></label>'+
            '<label class="module-setting-field"><span>日重算上限</span><input class="text-input module-hour-input" type="number" min="0" max="99" step="1" value="'+esc(txMaxReplans)+'" data-tianxing-config="max_replans_per_day"></label>'+
            '<label class="module-setting-field"><span>提前准备秒</span><input class="text-input module-hour-input" type="number" min="30" max="3600" step="30" value="'+esc(txPrepareLead)+'" data-tianxing-config="route_prepare_lead_sec"></label>'
          )+
          settingSection(
            '闭关攒点窗口',
            '仅影响闭关攒点；炼制全天可跑，并避让世界 boss 与探索消费窗口。',
            settingCheckbox('farm_window_enabled', '启用攒点窗口', txConfig.farm_window_enabled)+
            currentChoiceText('当前方式', txConfig.craft_farm_enabled ? '炼制' : (txConfig.retreat_farm_enabled ? '闭关' : '未启用'))+
            '<label class="module-setting-field module-setting-field-wide"><span>时段</span><input class="text-input module-name-input" type="text" value="'+esc(txFarmWindows)+'" data-tianxing-config="farm_windows_text"></label>'
          )+
          settingSection(
            '炼制攒天机',
            '不按时段限制；缺天机时优先用炼制补点，世界 boss 和探索消费窗口会自动让路。',
            settingCheckbox('craft_farm_enabled', '启用炼制', txConfig.craft_farm_enabled)+
            settingCheckbox('craft_farm_dry_run_enabled', '炼制试运行', txConfig.craft_farm_dry_run_enabled)+
            settingCheckbox('consume_conflicting_prediction_enabled', '冲突先消费', txConfig.consume_conflicting_prediction_enabled)+
            settingCheckbox('craft_farm_allow_unpredicted_override_enabled', '允许裸炼制', txConfig.craft_farm_allow_unpredicted_override_enabled)+
            '<label class="module-setting-field"><span>炼制物品</span><input class="text-input module-name-input" value="'+esc(txConfig.craft_farm_item || '玄铁剑')+'" data-tianxing-config="craft_farm_item"></label>'+
            '<label class="module-setting-field"><span>每日上限</span><input class="text-input module-hour-input" type="number" min="0" max="999" step="1" value="'+esc(txCraftLimit)+'" data-tianxing-config="craft_farm_daily_limit"></label>'+
            '<label class="module-setting-field"><span>间隔最小秒</span><input class="text-input module-hour-input" type="number" min="5" max="3600" step="5" value="'+esc(txCraftIntervalMin)+'" data-tianxing-config="craft_farm_interval_min_sec"></label>'+
            '<label class="module-setting-field"><span>间隔最大秒</span><input class="text-input module-hour-input" type="number" min="5" max="3600" step="5" value="'+esc(txCraftIntervalMax)+'" data-tianxing-config="craft_farm_interval_max_sec"></label>'+
            '<label class="module-setting-field"><span>回复超时秒</span><input class="text-input module-hour-input" type="number" min="30" max="1800" step="10" value="'+esc(txCraftTimeout)+'" data-tianxing-config="craft_farm_reply_timeout_sec"></label>'+
            currentChoiceText('炼制状态', (txCraftFarm.phase || 'idle')+' / '+(txCraftFarm.last_action || '无'))+
            currentChoiceText('今日轮次', String(txCraftFarm.daily_count || 0)+' / '+String(txCraftFarm.daily_limit || txCraftLimit || 0))+
            currentChoiceText('估算天机', String(txEstimatedTianji))+
            currentChoiceText('缺点策略', txShortagePolicy)+
            currentChoiceText('最近结果', txCraftFarm.last_result || txCraftFarm.last_error || '无')
          )+
          settingSection(
            '闭关攒天机',
            '用普通闭关攒天机；与深度闭关互斥，需授权才会强行出关或用丹。',
            settingCheckbox('retreat_farm_enabled', '启用闭关', txConfig.retreat_farm_enabled)+
            settingCheckbox('retreat_farm_dry_run_enabled', '闭关试运行', txConfig.retreat_farm_dry_run_enabled)+
            settingCheckbox('retreat_farm_allow_force_exit', '允许强行出关', txConfig.retreat_farm_allow_force_exit)+
            settingCheckbox('retreat_farm_allow_heqi_dan', '允许合气丹', txConfig.retreat_farm_allow_heqi_dan)+
            settingCheckbox('retreat_farm_auto_exchange_heqi_dan', '缺丹自动兑换', txConfig.retreat_farm_auto_exchange_heqi_dan)+
            '<label class="module-setting-field"><span>兑换数量</span><input class="text-input module-hour-input" type="number" min="1" max="999" step="1" value="'+esc(txConfig.retreat_farm_heqi_exchange_count || 10)+'" data-tianxing-config="retreat_farm_heqi_exchange_count"></label>'+
            settingCheckbox('retreat_farm_auto_donate_lingshi', '贡献不足捐灵石', txConfig.retreat_farm_auto_donate_lingshi)+
            settingCheckbox('deep_retreat_consume_enabled', '深闭消费改命', txConfig.deep_retreat_consume_enabled)+
            '<label class="module-setting-field"><span>捐献灵石</span><input class="text-input module-hour-input" type="number" min="1" max="99999" step="1" value="'+esc(txConfig.retreat_farm_donate_lingshi_count || 200)+'" data-tianxing-config="retreat_farm_donate_lingshi_count"></label>'+
            currentChoiceText('闭关状态', (txRetreatFarm.phase || 'idle')+' / '+(txRetreatFarm.last_action || '无'))+
            currentChoiceText('下次闭关', txRetreatFarm.next_time || '未设置')+
            currentChoiceText('最近结果', txRetreatFarm.last_result || txRetreatFarm.last_error || '无')
          )+
          settingSection(
            '卡住处理',
            '没读到回复先查盘；仍不准就停住，避免乱发。',
            '<label class="module-setting-field"><span>确认超时</span><input class="text-input module-hour-input" type="number" min="15" max="900" step="5" value="'+esc(txAckTimeout)+'" data-tianxing-config="ack_timeout_sec"></label>'+
            '<label class="module-setting-field"><span>校准间隔</span><input class="text-input module-hour-input" type="number" min="60" max="3600" step="30" value="'+esc(txCalibrationBackoff)+'" data-tianxing-config="calibration_backoff_sec"></label>'
          )+
          settingSection(
            '当前观测',
            '来自真实文案；过旧会先校准。',
            currentChoiceText('可选命星', availableStars)+
            currentChoiceText('推命', (tianxing.current_prediction || '无')+' / '+(tianxing.current_prediction_until || '未设置'))+
            currentChoiceText('改命', (tianxing.current_change || '无')+' / '+(tianxing.current_change_until || '未设置'))+
            currentChoiceText('命中/落空/改命', String(tianxing.hit_count || 0)+'/'+String(tianxing.miss_count || 0)+'/'+String(tianxing.change_count || 0))+
            currentChoiceText('自动接管', txPauseText)+
            currentChoiceText('下次自动', tianxing.auto_next_time || '未设置')+
            currentChoiceText('最近计划', tianxing.auto_last_plan || '无')
          )+
          settingSection(
            '执行状态',
            '只读，用来判断卡在发送、确认、校准还是放行。',
            currentChoiceText('阶段', txPhaseText)+
            currentChoiceText('路线', txTimeline.route || '无')+
            currentChoiceText('原因', txTimeline.reason || '无')+
            currentChoiceText('当前步骤', txActiveStepText)+
            currentChoiceText('确认截止', txActiveStep.ack_due_at || '未设置')+
            currentChoiceText('阻断至', txTimeline.blocked_until || '未设置')+
            currentChoiceText('已放行', txReleasedRoutes)+
            currentChoiceText('最近审计', txAuditText)+
            currentChoiceText('最近提示', txLastErrorText || '无')
          );
      }else if(module.name === '慕兰烽烟' || module.name === '慕兰'){
        var mulan = identity.mulan || {};
        moduleNote = '<div class="module-note">阶段：'+esc(mulan.phase || 'idle')+
          '｜下次 '+esc(mulan.next_time || '未设置')+
          '｜当前 '+esc(mulan.current_id || '无')+
          '｜公开 '+esc(mulan.public_id || '无')+
          '｜支援 '+esc(mulan.support_action || '无')+'</div>';
        settingsTools =
          settingSection(
            '慕兰烽烟',
            '搜集军报后复用当天共享情报；未知文本只辨一条，公开真报后按情报方向支援，无法确定时校准军功面板并保守支援。',
            renderModuleToggle('慕兰烽烟','开关')+
            currentChoiceText('候选编号', mulan.pending_ids || '1,2,3')+
            currentChoiceText('真报文本', mulan.public_text || '无')+
            currentChoiceText('支援动作', mulan.support_action || '无')+
            currentChoiceText('待回复', mulan.reply_to_msg_id ? String(mulan.reply_to_msg_id)+' / '+(mulan.reply_due_at || '未设置') : '无')+
            currentChoiceText('最近命令', mulan.last_command || '无')+
            currentChoiceText('最近结果', mulan.last_result || '无')+
            currentChoiceText('最近异常', mulan.last_error || '无')+
            currentChoiceText('完成轮次', String(mulan.cycle_count || 0))
          );
      }else if(module.name === '婉心封魂'){
        var wanxin = identity.wanxin || {};
        var wxConfig = wanxin.auto_config || {};
        var wxAssist = wanxin.assist || {};
        var wxCommission = wanxin.commission || {};
        var wxCommissionText = wxCommission.id ? ('#'+String(wxCommission.id)+(wxCommission.accepted ? ' 已接取' : ' 待接取')) : '无委托';
        moduleNote = '<div class="module-note">阶段：'+esc(wanxin.stage || '未记录')+
          '｜婉心 '+esc(wanxin.wanxin || 0)+
          '｜魂封 '+esc(wanxin.soul_seal || 0)+
          '｜咒源 '+esc(wanxin.curse_source || 0)+
          '｜委托 '+esc(wxCommissionText)+'</div>';
        settingsTools =
          settingSection(
            '自身推进',
            '探望每日一次；护持与推演按冷却推进。关闭单项后只停对应动作，不影响状态解析。',
            renderModuleToggle('婉心封魂','开关')+
            wanxinCheckbox('visit_enabled', '探望南宫婉', wxConfig.visit_enabled)+
            wanxinCheckbox('protect_enabled', '护持神魂', wxConfig.protect_enabled)+
            wanxinCheckbox('deduce_enabled', '推演封魂咒', wxConfig.deduce_enabled)+
            currentChoiceText('下次探望', wanxin.next_visit_time || '未设置')+
            currentChoiceText('下次护持', wanxin.next_protect_time || '未设置')+
            currentChoiceText('下次推演', wanxin.next_deduce_time || '未设置')
          )+
          settingSection(
            '解咒委托',
            '委托方只发布一次；协助方必须是阴罗宗身份，按委托方锚点回复辨咒或借幡。',
            wanxinCheckbox('publish_enabled', '自动发布委托', wxConfig.publish_enabled)+
            wanxinCheckbox('assist_enabled', '启用阴罗协助', wxConfig.assist_enabled)+
            '<label class="module-setting-field"><span>灵石</span><input class="text-input module-hour-input" type="number" min="1" max="1000000" step="1" value="'+esc(wxConfig.reward_lingshi || 1)+'" data-wanxin-config="reward_lingshi"></label>'+
            '<label class="module-setting-field"><span>协助ID</span><input class="text-input module-name-input" type="number" min="1" step="1" value="'+esc(wxAssist.send_as_id || '')+'" data-wanxin-config="assist_send_as_id"></label>'+
            currentChoiceText('委托', wxCommissionText)+
            currentChoiceText('委托方', wxCommission.owner_username ? '@'+wxCommission.owner_username : '未记录')+
            currentChoiceText('协助方', wxCommission.helper_username ? '@'+wxCommission.helper_username : (wxAssist.send_as_label || '未记录'))
          )+
          settingSection(
            '阴罗协助动作',
            '剥离咒源真实成功文案尚未稳定，默认关闭；打开后仍按冷却、锚点和安全锁执行。',
            wanxinCheckbox('identify_enabled', '辨认咒纹', wxAssist.identify_enabled)+
            wanxinCheckbox('banner_enabled', '借幡镇魂', wxAssist.banner_enabled)+
            wanxinCheckbox('strip_enabled', '剥离咒源', wxAssist.strip_enabled)+
            currentChoiceText('下次辨咒', wxAssist.next_identify_time || '未设置')+
            currentChoiceText('下次借幡', wxAssist.next_banner_time || '未设置')+
            currentChoiceText('下次剥离', wxAssist.next_strip_time || '未设置')+
            currentChoiceText('回复锚点', wxAssist.last_anchor_msg_id || '无')
          )+
          settingSection(
            '调度状态',
            '保存只更新策略，不会立即强制发送；下一轮由模块冷却和安全锁决定。',
            currentChoiceText('下次自动', wanxin.auto_next_time || '未设置')+
            currentChoiceText('最近动作', wanxin.auto_last_action || '无')+
            currentChoiceText('最近结果', wanxin.auto_last_result || '无')+
            currentChoiceText('最近异常', wanxin.auto_last_error || wxAssist.last_error || '无')+
            '<button type="button" class="btn btn-secondary" data-save-wanxin-config="1">保存设置</button>'
          );
      }else if(module.name === '玄骨考校'){
        moduleNote = '<div class="module-note">极阴：'+esc(identity.jiyin_effective_choice_label || identity.jiyin_choice_label || '未设置')+
          '｜南陇：'+esc(identity.nanlong_effective_choice_label || identity.nanlong_choice_label || '未设置')+'</div>';
        settingsTools =
          settingSection(
            '玄骨考校',
            '监听题目后按题库或辅助策略作答。',
            renderModuleToggle('玄骨考校','开关')
          )+
          settingSection(
            '极阴祖师抉择',
            '当前：'+(identity.jiyin_effective_choice_label || identity.jiyin_choice_label || '未设置'),
            renderModuleToggle('极阴祖师','开关')+
            '<button type="button" class="btn btn-secondary" data-jiyin-choice="offer_soul">献魂</button>'+
            '<button type="button" class="btn btn-secondary" data-jiyin-choice="hide_aura">收敛</button>'+
            '<button type="button" class="btn btn-secondary" data-jiyin-choice="auto">自动</button>'
          )+
          settingSection(
            '南陇侯兑换',
            '当前：'+(identity.nanlong_effective_choice_label || identity.nanlong_choice_label || '未设置'),
            renderModuleToggle('南陇侯','开关')+
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
          renderSmallWorldFeature(identity,'harvest','收割');
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
            renderSmallWorldFeature(identity,'barrier','护界')+
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
    reorderModuleCards(grid);
    renderModuleSettingsModal();
  };

  async function saveInlineWindowConfig(moduleName){
    if(typeof postJson !== 'function' || typeof appState === 'undefined'){
      return;
    }
    var root = null;
    var candidates = document.querySelectorAll('[data-inline-window-config]');
    for(var i = 0; i < candidates.length; i += 1){
      if((candidates[i].getAttribute('data-inline-window-config') || '') === moduleName){
        root = candidates[i];
        break;
      }
    }
    if(!root){
      return;
    }
    var startInput = root.querySelector('[data-inline-window-start]');
    var endInput = root.querySelector('[data-inline-window-end]');
    try{
      var data = await postJson('/api/module-window', {
        send_as_id: appState.selectedId,
        module: moduleName,
        start_hour_local: startInput ? startInput.value : 0,
        end_hour_local: endInput ? endInput.value : 0
      });
      if(typeof updateFlash === 'function'){
        updateFlash(data.message || '已更新执行窗口', false);
      }
      if(typeof applySnapshot === 'function'){
        applySnapshot(data.snapshot || appState.snapshot, {keepFlash: true});
      }
    }catch(error){
      if(typeof updateFlash === 'function'){
        updateFlash((error && error.message) || '执行窗口更新失败', true);
      }
      if(typeof renderAll === 'function'){
        renderAll();
      }
    }
  }

  async function saveInlinePetNames(){
    if(typeof postJson !== 'function' || typeof appState === 'undefined'){
      return;
    }
    var payload = {send_as_id: appState.selectedId};
    var inputs = document.querySelectorAll('[data-pet-inline-name]');
    inputs.forEach(function(input){
      payload[input.getAttribute('data-pet-inline-name')] = input.value || '';
    });
    try{
      var data = await postJson('/api/pet-name', payload);
      if(typeof updateFlash === 'function'){
        updateFlash(data.message || '已更新法宝名称', false);
      }
      if(typeof applySnapshot === 'function'){
        applySnapshot(data.snapshot || appState.snapshot, {keepFlash: true});
      }
    }catch(error){
      if(typeof updateFlash === 'function'){
        updateFlash((error && error.message) || '法宝名称更新失败', true);
      }
      if(typeof renderAll === 'function'){
        renderAll();
      }
    }
  }

  async function submitHehuanConfig(){
    if(typeof postJson !== 'function' || typeof appState === 'undefined'){
      return;
    }
    var input = document.querySelector('[data-hehuan-retry-max-min]');
    try{
      var data = await postJson('/api/hehuan-config', {
        send_as_id: appState.selectedId,
        retry_max_interval_min: input ? input.value : 5
      });
      if(typeof updateFlash === 'function'){
        updateFlash(data.message || '已更新合欢宗补发策略', false);
      }
      if(typeof applySnapshot === 'function'){
        applySnapshot(data.snapshot || appState.snapshot, {keepFlash: true});
      }
    }catch(error){
      if(typeof updateFlash === 'function'){
        updateFlash((error && error.message) || '合欢宗补发策略更新失败', true);
      }
      if(typeof renderAll === 'function'){
        renderAll();
      }
    }
  }

  function collectTianxingConfig(){
    var config = {};
    var controls = document.querySelectorAll('[data-tianxing-config]');
    controls.forEach(function(control){
      var key = control.getAttribute('data-tianxing-config') || '';
      if(!key){
        return;
      }
      if(control.type === 'checkbox'){
        config[key] = !!control.checked;
      }else{
        config[key] = control.value || '';
      }
    });
    return config;
  }

  async function submitTianxingConfig(){
    if(typeof postJson !== 'function' || typeof appState === 'undefined'){
      return;
    }
    try{
      var data = await postJson('/api/tianxing-config', {
        send_as_id: appState.selectedId,
        config: collectTianxingConfig()
      });
      if(typeof updateFlash === 'function'){
        updateFlash(data.message || '已更新天星宗策略', false);
      }
      if(typeof applySnapshot === 'function'){
        applySnapshot(data.snapshot || appState.snapshot, {keepFlash: true});
      }
    }catch(error){
      if(typeof updateFlash === 'function'){
        updateFlash((error && error.message) || '天星宗策略更新失败', true);
      }
      if(typeof renderAll === 'function'){
        renderAll();
      }
    }
  }

  function collectWanxinConfig(){
    var config = {};
    var controls = document.querySelectorAll('[data-wanxin-config]');
    controls.forEach(function(control){
      var key = control.getAttribute('data-wanxin-config') || '';
      if(!key){
        return;
      }
      if(control.type === 'checkbox'){
        config[key] = !!control.checked;
      }else{
        config[key] = control.value || '';
      }
    });
    return config;
  }

  async function submitWanxinConfig(){
    if(typeof postJson !== 'function' || typeof appState === 'undefined'){
      return;
    }
    try{
      var data = await postJson('/api/wanxin-config', {
        send_as_id: appState.selectedId,
        config: collectWanxinConfig()
      });
      if(typeof updateFlash === 'function'){
        updateFlash(data.message || '已更新婉心封魂策略', false);
      }
      if(typeof applySnapshot === 'function'){
        applySnapshot(data.snapshot || appState.snapshot, {keepFlash: true});
      }
    }catch(error){
      if(typeof updateFlash === 'function'){
        updateFlash((error && error.message) || '婉心封魂策略更新失败', true);
      }
      if(typeof renderAll === 'function'){
        renderAll();
      }
    }
  }

  async function submitExploreRiftRebirthConfig(){
    if(typeof postJson !== 'function' || typeof appState === 'undefined'){
      return;
    }
    var payload = {send_as_id: appState.selectedId};
    var controls = document.querySelectorAll('[data-explore-rift-rebirth-config]');
    controls.forEach(function(control){
      var key = control.getAttribute('data-explore-rift-rebirth-config') || '';
      if(key){
        payload[key] = control.value || '';
      }
    });
    try{
      var data = await postJson('/api/explore-rift-rebirth-config', payload);
      if(typeof updateFlash === 'function'){
        updateFlash(data.message || '已更新夺舍选择', false);
      }
      if(typeof applySnapshot === 'function'){
        applySnapshot(data.snapshot || appState.snapshot, {keepFlash: true});
      }
    }catch(error){
      if(typeof updateFlash === 'function'){
        updateFlash((error && error.message) || '夺舍选择更新失败', true);
      }
      if(typeof renderAll === 'function'){
        renderAll();
      }
    }
  }

  document.addEventListener('click', function(event){
    if(!event.target || !event.target.closest){
      return;
    }
    var saveWindowBtn = event.target.closest('[data-save-window-inline]');
    if(saveWindowBtn){
      saveInlineWindowConfig(saveWindowBtn.getAttribute('data-save-window-inline') || '');
      return;
    }
    if(event.target.closest('[data-save-pet-inline]')){
      saveInlinePetNames();
      return;
    }
    if(event.target.closest('[data-save-explore-rift-rebirth-config]')){
      submitExploreRiftRebirthConfig();
      return;
    }
    if(event.target.closest('[data-save-tianxing-config]')){
      submitTianxingConfig();
      return;
    }
    if(event.target.closest('[data-save-wanxin-config]')){
      submitWanxinConfig();
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

  document.addEventListener('change', function(event){
    if(!event.target || !event.target.closest){
      return;
    }
    if(event.target.closest('[data-hehuan-retry-max-min]')){
      submitHehuanConfig();
      return;
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
