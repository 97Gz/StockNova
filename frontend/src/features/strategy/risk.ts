/** 风险等级 → 文案与颜色（1~2 稳健 / 3 中等 / 4~5 激进） */
export function riskBadge(risk: number): { text: string; cls: string } {
  if (risk <= 2) return { text: `风险 ${risk}/5 · 稳健`, cls: 'bg-down/10 text-down' }
  if (risk === 3) return { text: `风险 ${risk}/5 · 中等`, cls: 'bg-gold/10 text-gold' }
  return { text: `风险 ${risk}/5 · 激进`, cls: 'bg-up/10 text-up' }
}
