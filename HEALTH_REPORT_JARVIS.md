# jarvis-graph-lite health report

**Repo**: `C:\JARVIS`

## 1. Headline

- **407** files, 1 parse errors
- **4314** symbols (1656 functions, 252 classes, 1001 methods)
- **3589** imports (15.6% resolved)
- **29004** call sites (16.6% resolved)

## 2. Complexity hotspots (cyclomatic ≥ 10)

- 2545 callables · avg cyclomatic = 5.67 · high = 194 · extreme = 97

| # | complexity | lines | function | file |
|--:|--:|--:|---|---|
| 1 | 184 | 934 | `tools.lyrc-local.amv_engine.render_amv` | `tools/lyrc-local/amv_engine.py:2117` |
| 2 | 156 | 474 | `tools.lyrc-local.amv_engine.beat_match_edit` | `tools/lyrc-local/amv_engine.py:955` |
| 3 | 91 | 385 | `agents.jarvis_status_api.StatusHandler.do_GET` | `agents/jarvis_status_api.py:211` |
| 4 | 75 | 496 | `tools.lyrc-local.amv_engine._generate_ass_subtitles` | `tools/lyrc-local/amv_engine.py:1448` |
| 5 | 71 | 296 | `tools.lyrc-local.amv_engine.detect_scenes` | `tools/lyrc-local/amv_engine.py:588` |
| 6 | 70 | 196 | `agents.jarvis_sparring_engine.gather_system_context` | `agents/jarvis_sparring_engine.py:77` |
| 7 | 67 | 228 | `tools.lyrc-local.amv_engine.analyze_render_quality` | `tools/lyrc-local/amv_engine.py:3491` |
| 8 | 58 | 277 | `agents.autonomous_agent.AutonomousAgent.run` | `agents/autonomous_agent.py:195` |
| 9 | 58 | 126 | `tools.lyrc-local.server.APIHandler.do_GET` | `tools/lyrc-local/server.py:384` |
| 10 | 57 | 55 | `agents.autonomous_agent.extract_error_type` | `agents/autonomous_agent.py:526` |
| 11 | 47 | 131 | `tools.lyrc-local.amv_engine.get_file_info` | `tools/lyrc-local/amv_engine.py:7918` |
| 12 | 46 | 155 | `tools.lyrc-local.amv_engine.validate_upload` | `tools/lyrc-local/amv_engine.py:6360` |
| 13 | 45 | 122 | `agents.jarvis_autopilot.mode_digest` | `agents/jarvis_autopilot.py:152` |
| 14 | 45 | 139 | `agents.jarvis_brain.mode_health` | `agents/jarvis_brain.py:1172` |
| 15 | 43 | 136 | `agents.jarvis_brain.mode_status` | `agents/jarvis_brain.py:902` |

## 3. Long functions (≥ 50 lines)

- 343 of 2545 callables over threshold · avg = 26.34 lines

| # | lines | cyclomatic | function | file |
|--:|--:|--:|---|---|
| 1 | 934 | 184 | `tools.lyrc-local.amv_engine.render_amv` | `tools/lyrc-local/amv_engine.py:2117` |
| 2 | 496 | 75 | `tools.lyrc-local.amv_engine._generate_ass_subtitles` | `tools/lyrc-local/amv_engine.py:1448` |
| 3 | 474 | 156 | `tools.lyrc-local.amv_engine.beat_match_edit` | `tools/lyrc-local/amv_engine.py:955` |
| 4 | 430 | 1 | `dashboard.dashboard_server.create_html_template` | `dashboard/dashboard_server.py:184` |
| 5 | 398 | 1 | `tools.voie-omega.alembic.versions.0001_initial.upgrade` | `tools/voie-omega/alembic/versions/0001_initial.py:24` |
| 6 | 385 | 91 | `agents.jarvis_status_api.StatusHandler.do_GET` | `agents/jarvis_status_api.py:211` |
| 7 | 296 | 71 | `tools.lyrc-local.amv_engine.detect_scenes` | `tools/lyrc-local/amv_engine.py:588` |
| 8 | 277 | 58 | `agents.autonomous_agent.AutonomousAgent.run` | `agents/autonomous_agent.py:195` |
| 9 | 267 | 17 | `tools.lyrc-local.amv_engine.generate_render_dashboard` | `tools/lyrc-local/amv_engine.py:5326` |
| 10 | 262 | 10 | `products.create_laufplan_pdf.create_pdf` | `products/create_laufplan_pdf.py:25` |
| 11 | 253 | 27 | `tools.voie-omega.voie.services.verification_service.verify_discovery_event` | `tools/voie-omega/voie/services/verification_service.py:181` |
| 12 | 244 | 1 | `core.advanced_templates.AdvancedTemplates.get_database_tool` | `core/advanced_templates.py:219` |
| 13 | 231 | 7 | `protocol.integrated_workflow.IntegratedWorkflow.execute` | `protocol/integrated_workflow.py:39` |
| 14 | 228 | 67 | `tools.lyrc-local.amv_engine.analyze_render_quality` | `tools/lyrc-local/amv_engine.py:3491` |
| 15 | 222 | 1 | `core.advanced_templates.AdvancedTemplates.get_automation_script` | `core/advanced_templates.py:465` |

## 4. God files (composite of symbols × LOC × fan-in)

| # | score | symbols | LOC | fan-in | file |
|--:|--:|--:|--:|--:|---|
| 1 | 0.683 | 104 | 8920 | 2 | `tools/lyrc-local/amv_engine.py` |
| 2 | 0.468 | 37 | 436 | 40 | `agents/agent_tools.py` |
| 3 | 0.417 | 86 | 3776 | 0 | `tools/lyrc-local/server.py` |
| 4 | 0.273 | 36 | 637 | 16 | `agents/jarvis_memory_long_term.py` |
| 5 | 0.222 | 18 | 161 | 19 | `agents/jarvis_activity_log.py` |
| 6 | 0.165 | 24 | 794 | 7 | `agents/claude_bridge.py` |
| 7 | 0.15 | 35 | 1009 | 0 | `agents/jarvis_brain.py` |
| 8 | 0.149 | 35 | 751 | 1 | `voice/jarvis_hud_v2.py` |
| 9 | 0.143 | 7 | 113 | 14 | `tools/jarvis-graph-lite/tests/conftest.py` |
| 10 | 0.143 | 5 | 50 | 15 | `tools/jarvis-graph-lite/src/jarvis_graph/db.py` |
| 11 | 0.131 | 32 | 538 | 1 | `tools/jarvis-graph-lite/src/jarvis_graph/cli.py` |
| 12 | 0.131 | 25 | 700 | 3 | `agents/core/security_v3.py` |
| 13 | 0.127 | 33 | 578 | 0 | `agents/jarvis_payment_setup.py` |
| 14 | 0.121 | 24 | 964 | 1 | `core/agent_controller_v5.py` |
| 15 | 0.116 | 29 | 617 | 0 | `agents/jarvis_daily_brief.py` |

## 5. Dead code candidates

- 242 candidates after filtering 2909 symbols
  (excluded: dunder=112, private=740, entrypoint=129, test=93, textual=142)

Top files by dead-symbol count:

| count | file |
|--:|---|
| 18 | `tools/voie-omega/voie/cli/main.py` |
| 11 | `tools/lyrc-local/server.py` |
| 9 | `monitoring/metrics_collector.py` |
| 9 | `tools/voie-omega/voie/core/settings.py` |
| 8 | `agents/agent_tools.py` |
| 8 | `agents/core/knowledge_graph.py` |
| 8 | `tools/voie-omega/voie/db/models.py` |
| 7 | `api/innovation_api.py` |
| 6 | `agents/core/multi_language.py` |
| 6 | `agents/core/performance.py` |
| 6 | `agents/core/self_improvement.py` |
| 6 | `jarvis.py` |
| 5 | `action_logger.py` |
| 5 | `core/innovation_service.py` |
| 5 | `git/version_control.py` |

## 6. Unused imports

- 323 unused of 3589 imports

Top files by unused-import count:

| count | file |
|--:|---|
| 11 | `workspace/merged_projects/merged_ultimate_20260304_003525/main.py` |
| 11 | `workspace/merged_projects/merged_ultimate_20260304_004525/main.py` |
| 9 | `agents/orchestrator_master.py` |
| 6 | `agents/orchestrator_v2.py` |
| 6 | `core/agent_controller_v5.py` |
| 5 | `agents/core/security_v3.py` |
| 5 | `agents/jarvis_brain.py` |
| 5 | `agents/jarvis_seo_tracker.py` |
| 5 | `memory/journal_to_knowledge.py` |
| 5 | `vision/vision_server_v2.py` |
| 4 | `agents/jarvis_autopilot.py` |
| 4 | `core/agent_controller.py` |
| 4 | `core/background_manager.py` |
| 4 | `perception/screen_memory.py` |
| 3 | `agents/autonomous_agent.py` |

## 7. Circular dependencies

- 2 cycle(s) on 407 files / 310 resolved edges

**Cycle 1** (size 2)
- `agents/agent_tools.py`
- `agents/jarvis_task_injector.py`

**Cycle 2** (size 1)
- `tools/lyrc-local/amv_engine.py`

