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

  function tianxingRouteSelect(key, selected){
    var options = [{value:'auto', label:'自动'}, '探索', '闭关', '炼制', '斗法'];
    return '<label class="module-setting-field"><span>'+esc(key === 'predict_route' ? '推命' : '改命')+'</span>'+
      '<select class="text-input" data-tianxing-config="'+esc(key)+'">'+optionHtml(options, selected || 'auto')+'</select></label>';
  }

  function moduleCardPriority(title){
    var normalized = String(title || '').trim();
    if(normalized === '日常'){
      return 0;
    }
    if(['合欢宗','天星宗','阴罗宗'].indexOf(normalized) >= 0){
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
    var hiddenModules = new Set(['温养器灵','器灵试炼','天机代卜','共历心劫','侍妾远航','极阴祖师','南陇侯','点卯','宗门传功','闯塔','深度闭关','卜筮问天','斗法','探寻裂缝']);

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
            '名称保存',
            '修改目标名后保存；留空的温养或试炼目标会沿用抚摸目标。',
            '<button type="button" class="btn btn-secondary" data-save-pet-inline="1">保存名称</button>'
          );
        return renderModuleCard('法宝', moduleNote, primaryTools, settingsTools, compactDetails(['法宝','温养器灵','器灵试炼']), null);
      }else if(module.name === '野外历练'){
        var dailyNames = ['野外历练','点卯','宗门传功','闯塔','深度闭关','卜筮问天','斗法'];
        var checkinWin = identity.checkin_window_local || {};
        var towerWin = identity.tower_window_local || {};
        moduleNote = '<div class="module-note">野外：'+esc(identity.wild_training_strategy || '深入')+
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
            '目标和次数按斗法模块配置执行。',
            renderModuleToggle('斗法','开关')+
            currentChoiceText('目标', identity.duel_target || '未配置')+
            currentChoiceText('进度', String(identity.duel_completed_count || 0)+'/'+String(identity.duel_total_count || 0))
          );
        return renderModuleCard('日常', moduleNote, primaryTools, settingsTools, compactDetails(dailyNames), null);
      }else if(module.name === '元婴'){
        var riftModule = moduleByName('探寻裂缝');
        var riftStatus = riftModule ? (riftModule.enabled ? '开' : '关') : '不可用';
        moduleNote = '<div class="module-note">元婴：'+esc(identity.yuanying_level_text || '未读取')+'｜裂缝：'+esc(riftStatus)+'</div>';
        settingsTools =
          settingSection(
            '元婴出窍',
            '元婴链路按被动结算和冷却推进。',
            renderModuleToggle('元婴','开关')
          )+
          settingSection(
            '探寻裂缝',
            '裂缝探寻归到元婴卡片统一管理。',
            renderModuleToggle('探寻裂缝','开关')
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
        var availableStars = (tianxing.available_stars || []).join('、') || '未记录';
        moduleNote = '<div class="module-note">命星：'+esc(tianxing.fixed_star || '未定')+
          '｜天机 '+esc(tianxing.tianji_value || 0)+
          '｜逆命劫 '+esc(tianxing.calamity_count || 0)+
          '｜dry-run '+(txConfig.strategy_dry_run_enabled ? '开' : '关')+'</div>';
        settingsTools =
          settingSection(
            '状态校准',
            '查盘和观命用于校准真实状态；消劫会消耗修为和宗门贡献，默认沿用旧逻辑开启。',
            settingCheckbox('auto_panel_enabled', '自动查盘', txConfig.auto_panel_enabled)+
            settingCheckbox('auto_observe_enabled', '自动观命', txConfig.auto_observe_enabled)+
            settingCheckbox('auto_clear_calamity_enabled', '自动消劫', txConfig.auto_clear_calamity_enabled)+
            '<label class="module-setting-field"><span>消劫阈值</span><input class="text-input module-hour-input" type="number" min="1" max="99" step="1" value="'+esc(txConfig.min_calamity_to_clear || 1)+'" data-tianxing-config="min_calamity_to_clear"></label>'+
            '<label class="module-setting-field"><span>校准间隔</span><input class="text-input module-hour-input" type="number" min="1" max="24" step="1" value="'+esc(txConfig.status_backoff_hours || 6)+'" data-tianxing-config="status_backoff_hours"></label>'
          )+
          settingSection(
            '战略动作',
            '定命、推命、改命可能影响后续模块路线；默认关闭且 dry-run 开启时只记录计划不发送。',
            settingCheckbox('auto_set_star_enabled', '自动定命', txConfig.auto_set_star_enabled)+
            settingCheckbox('auto_predict_enabled', '自动推命', txConfig.auto_predict_enabled)+
            settingCheckbox('auto_change_fate_enabled', '自动改命', txConfig.auto_change_fate_enabled)+
            settingCheckbox('strategy_dry_run_enabled', '战略 dry-run', txConfig.strategy_dry_run_enabled)
          )+
          settingSection(
            '命星与路线',
            '优先级用逗号或顿号分隔；只会从真实可选命星里挑选。',
            '<label class="module-setting-field module-setting-field-wide"><span>命星优先</span><input class="text-input module-name-input" value="'+esc((txConfig.star_priority || []).join('、'))+'" data-tianxing-config="star_priority"></label>'+
            '<label class="module-setting-field module-setting-field-wide"><span>路线优先</span><input class="text-input module-name-input" value="'+esc((txConfig.route_priority || []).join('、'))+'" data-tianxing-config="route_priority"></label>'+
            tianxingRouteSelect('predict_route', txConfig.predict_route)+
            tianxingRouteSelect('change_route', txConfig.change_route)+
            '<label class="module-setting-field"><span>改命天机</span><input class="text-input module-hour-input" type="number" min="3" max="999" step="1" value="'+esc(txConfig.min_tianji_for_change || 6)+'" data-tianxing-config="min_tianji_for_change"></label>'
          )+
          settingSection(
            '当前观测',
            '这些数据来自真实文案或消息盒子，状态过旧时自动动作会先查盘校准。',
            currentChoiceText('可选命星', availableStars)+
            currentChoiceText('推命', (tianxing.current_prediction || '无')+' / '+(tianxing.current_prediction_until || '未设置'))+
            currentChoiceText('改命', (tianxing.current_change || '无')+' / '+(tianxing.current_change_until || '未设置'))+
            currentChoiceText('命中/落空/改命', String(tianxing.hit_count || 0)+'/'+String(tianxing.miss_count || 0)+'/'+String(tianxing.change_count || 0))+
            currentChoiceText('下次自动', tianxing.auto_next_time || '未设置')+
            currentChoiceText('最近计划', tianxing.auto_last_plan || '无')
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
    if(event.target.closest('[data-tianxing-config]')){
      submitTianxingConfig();
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
