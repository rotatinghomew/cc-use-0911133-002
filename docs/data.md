# 登记和核查资料

`fixtures/completeness.json` 使用本地合成居民键，不包含真实身份资料。统一键已经确认，来源中的同键表示同一居民，不需要再次猜测身份关系。

`records` 是各来源在服务窗口中的登记，修订号更新同一 observation_id，撤回记录不再贡献来源出现。`opportunities` 表示地区、窗口和来源是否实际开展采集；来源关闭与开展后未见居民是不同状态。

`audit_frames` 是独立核查的抽样框，eligible_units 是可核查对象数，不是已确认的服务人数。`audits` 是已完成抽样，selection_probability 为该对象入样概率。`followup_audits` 是下一次核查的返回资料，尚未纳入首轮。核查预算和各框可使用名额分别存放。

`fixtures/demo.json` 仍使用原登记入口，批量分析资料不属于它的输入格式。

## 完整性分析入口

```bash
python3 -m registry_delta_hub.completeness fixtures/completeness.json --output /tmp/registry-completeness
```

输出三个文件：

- `report.md`：中文报告，含去重人数、各层来源依赖、候选模型比较、独立核查检验、可估/不可估分层与核查名额建议；
- `summary.json`：给下游排期用的机器摘要；
- `payload.json`：完整证据，供复核。

分析口径要点：

1. **行数不是人数。** 按 `observation_id` 取最高 `revision`，最新为 `withdrawn` 的观测从登记中移除；合并后的 153 行解析为 71 名居民。
2. **来源未工作不等于漏登。** 只使用 `opportunities` 中该地区/窗口实际工作的来源；声明工作但零记录的来源会单独标出（可能是静默停采）。
3. **窗口分层，不合并成封闭总体。** 每个地区/窗口单独估计；跨窗口出现的居民只计一次。
4. **候选模型并列。** 朴素独立捕获-再捕获、剔除抄送/包含来源后的独立模型、同质覆盖率外推、设计无关上下界、核查校准同时报告，而不是只选最贴合重合表的一个。
5. **核查用于检验而非拟合。** 核查是独立概率样本，按 `selection_probability` 做 Horvitz-Thompson 加权；`followup_audits` 默认不纳入首轮，重新分析时单独归因。没有 `selection_probability` 的记录不进入设计估计。
6. **不可识别就报告不可识别。** 来源抄送/机械包含会摧毁再捕获对照；核查框与登记窗口人群不一致时，并列报告不同口径而不是给出单一精确数字。
