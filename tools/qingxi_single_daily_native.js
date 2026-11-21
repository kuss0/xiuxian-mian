  // Injected into the pinned assistant closure; the vendor file stays intact.
  async function completeSingleDaily(id) {
    if (id !== __SINGLE_DAILY_ID__) throw new Error('single_daily_scope');
    const row = DAILY.find(item => item.id === id);
    if (!row) throw new Error('single_daily_unknown');
    const live = readCombat();
    if (live?.active && !live.overworld) throw new Error('single_daily_existing_combat');
    return batchWorkflow('single_daily', async owner => {
      await storyReady(owner);
      state.batch.current = row.id;
      let status = await dailyStatus(row.id, owner);
      storyCheck(owner); batchDay(status.day);
      if (dailyEntrySkip(row, status)) throw new Error('single_daily_entry_changed');
      let swap = null;
      if (row.id === 'moonpalace' && CFG.moonSwapHitPassive && !instanceCombatActive())
        swap = await stripHitPassives(owner);
      try {
        const issue = row.id === 'moonpalace' && moonEntryIssue(swap);
        if (issue) throw new Error('single_daily_loadout_blocked');
        await prepareBatchPortal(owner);
        await travelMenu(INSTANCE_LABELS[row.id], owner);
        status = await dailyStatus(row.id, owner);
        storyCheck(owner); batchDay(status.day);
        if (dailyEntrySkip(row, status)) throw new Error('single_daily_entry_changed');
        const result = await instanceObjective(null, {instance:row.id}, owner);
        if (result?.singleDefeat === true) {
          state.batch.outcome = 'defeat';
        } else {
          await confirmDailyClaim(row.id, row.name, owner);
          state.batch.completed.push({id:row.id, name:row.name, day:state.batch.day});
          state.batch.outcome = 'claimed';
        }
        if (!act('stop')) throw new Error('single_daily_exit_refused');
        return row.name + (state.batch.outcome === 'claimed' ? '已确认领奖' : '战败，停止本轮');
      } finally {
        if (swap?.changed) await finishMoonLoadout(owner);
      }
    });
  }
