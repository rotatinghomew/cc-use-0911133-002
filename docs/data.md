# 登记和核查资料

`fixtures/completeness.json` 使用本地合成居民键，不包含真实身份资料。统一键已经确认，来源中的同键表示同一居民，不需要再次猜测身份关系。

`records` 是各来源在服务窗口中的登记，修订号更新同一 observation_id，撤回记录不再贡献来源出现。`opportunities` 表示地区、窗口和来源是否实际开展采集；来源关闭与开展后未见居民是不同状态。

`audit_frames` 是独立核查的抽样框，eligible_units 是可核查对象数，不是已确认的服务人数。`audits` 是已完成抽样，selection_probability 为该对象入样概率。`followup_audits` 是下一次核查的返回资料，尚未纳入首轮。核查预算和各框可使用名额分别存放。

`fixtures/demo.json` 仍使用原登记入口，批量分析资料不属于它的输入格式。
