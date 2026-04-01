基于以下讨论，判断参会者是否达成共识。

请输出严格的 JSON 格式（不要有其他内容）：
{
  "consensus_reached": true或false,
  "consensus_level": "full或partial或none",
  "agreed_points": ["共识点1", "共识点2"],
  "disputed_points": ["分歧点1", "分歧点2"],
  "recommendation": "继续讨论/进入下一阶段/可以定稿"
}

判断标准：
- full：所有参会者对核心方案无实质异议
- partial：核心方向一致，但细节有分歧
- none：核心方案存在根本分歧

讨论内容：

{latest_round}
