import { SponsorCard } from '@/components/common/SponsorDialog'
import { DataManageCard } from '@/features/settings/DataManageCard'
import { ExtDataCard } from '@/features/settings/ExtDataCard'
import { PromptsCard } from '@/features/settings/PromptsCard'
import { SettingsForm } from '@/features/settings/SettingsForm'
import { StatsCard } from '@/features/settings/StatsCard'
import { SyncLogsCard } from '@/features/settings/SyncLogsCard'

/**
 * 设置中心：Bento 布局
 * ┌────────────── 数据管理(7) ─────────────┬──── 库存统计(5) ────┐
 * ├── 数据同步(4) ──┬── 实时行情(4) ──┬──── AI 接入(4) ────────┤
 * ├──────────────────────── AI 提示词(12) ───────────────────────┤
 * ├────────────── 同步历史(7) ─────────────┬─── 扩展数据(5) ────┤
 * └──────────────────────────────────────────────────────────────┘
 * AI 提示词紧跟 AI 接入区（同属 AI 配置域，调完接入顺手调提示词）；
 * 同步历史/扩展数据属于低频查看的运维信息，沉底。
 * 主题与外观配置在顶栏即可切换（无需进设置页）；托盘/自启在 M7 加入。
 */
export function SettingsPage() {
  return (
    <div className="grid grid-cols-12 gap-4">
      <DataManageCard />
      <StatsCard />
      <SettingsForm />
      <PromptsCard />
      <SyncLogsCard />
      <ExtDataCard />
      <SponsorCard />
    </div>
  )
}
