# Specification Quality Checklist: serve 常驻服务（HTTP API + MCP，模型常驻消除冷启动）

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-10-04
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`
- 本规格未使用任何 `[NEEDS CLARIFICATION]` 标记：范围内的空白均有合理默认，且默认值来自项目既有
  蓝图（`file2kg-README.md` 的 L3 安全项「MCP 写操作权限 = 只读」）与刚批准的项目宪法 v1.0.0。
  所有推断出的默认已集中记录在 Assumptions 一节，供审阅时推翻。
- 最值得复核的三条假设（按影响面排序）——**已由用户于 2026-10-04 逐条确认，plan 阶段按此执行，
  无需重新讨论**：
  1. **写范围**——写权限开启后只暴露「增量摄取」，删除与全量重建仍留在命令行入口（US3 / FR-013）
  2. **MCP 与 HTTP 共用同一个常驻服务**——因此 Agent 走 MCP 也享受模型常驻（US2 场景 5）
  3. **单库 + 本机单用户**——不做多库路由，不做认证与远程暴露（Assumptions）
- **预热默认关闭（FR-006）已由用户于 2026-10-04 确认**：默认走懒加载，首次请求付一次冷启动，
  之后全程常驻；只有显式请求才在启动时预热。这是宪法原则 II「预热 MUST NOT 成为默认副作用」
  的直接落地，plan 阶段 MUST 保持这一默认。
- 宪法 v1.0.0 合规自查（供 `/speckit-plan` 的 Constitution Check 复核）：

  | 原则 | 落点 |
  |------|------|
  | I 本地优先，零 Key 默认 | FR-003（默认无需 key）、FR-002（默认只绑回环） |
  | II 懒惰即设计 | FR-005（加载一次常驻）、FR-006（预热非默认）、FR-007（只读不碰模型） |
  | III 写操作默认只读 (NON-NEGOTIABLE) | FR-008/009/011/012/013、US2 场景 4、US3 |
  | IV 审计诚实 (NON-NEGOTIABLE) | FR-010、FR-016、FR-018、US3 场景 4/5、SC-005/SC-008 |
  | V 一库一模 (NON-NEGOTIABLE) | FR-014、US4 场景 2、Edge Case「模型与库不匹配」 |
