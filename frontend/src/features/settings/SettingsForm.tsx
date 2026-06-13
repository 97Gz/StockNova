import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Loader2 } from 'lucide-react'
import { useState } from 'react'

import { Portal } from '@/components/common/Portal'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import type { SettingItem } from '@/lib/api'
import {
  ApiError,
  fetchSettings,
  saveSettings,
  testAiConnection,
  testNotify,
  testQuoteSource,
} from '@/lib/api'

/**
 * 配置表单：按分组渲染后端注册表里的全部配置项。
 * 后端是唯一事实来源——新增配置项时前端零改动（按 type 自动选控件）。
 */

/** 分组定义：key 前缀 → 卡片标题与说明 */
const GROUPS: { prefix: string; title: string; desc: string }[] = [
  { prefix: 'data.', title: '数据同步', desc: '历史数据初始化与每日增量的行为' },
  { prefix: 'quotes.', title: '实时行情', desc: '盘中报价轮询的数据源与频率' },
  { prefix: 'ai.', title: 'AI 接入', desc: 'OpenAI 兼容协议，默认 DeepSeek（M5 全面启用）' },
  { prefix: 'report.', title: '定时研报', desc: '盘后自动对自选+持仓批量诊断，归档到研报库' },
  { prefix: 'notify.', title: '推送通道', desc: '研报/提醒推送到 IM 与邮箱，配了哪个推哪个' },
]

/** 个别配置项的专用控件（下拉选项等） */
const SELECT_OPTIONS: Record<string, { value: string; label: string }[]> = {
  'quotes.source': [
    { value: 'tencent', label: '腾讯（推荐）' },
    { value: 'sina', label: '新浪（备用）' },
  ],
  'report.mode': [
    { value: 'quick', label: '快速（省 token）' },
    { value: 'deep', label: '深度（完整工作流）' },
  ],
}

/** 推送通道中文名（测试结果展示用） */
const CHANNEL_LABEL: Record<string, string> = {
  wecom: '企业微信',
  feishu: '飞书',
  telegram: 'Telegram',
  email: '邮件',
}

export function SettingsForm() {
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['settings'], queryFn: fetchSettings })

  // 草稿：用户改过但未保存的值（key → value）
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [savedFlash, setSavedFlash] = useState(false)

  const save = useMutation({
    mutationFn: () => saveSettings(draft),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['settings'] })
      setDraft({}) // 保存成功后清空草稿，表单回到服务端最新值
      setSavedFlash(true)
      setTimeout(() => setSavedFlash(false), 2000)
    },
  })

  const quoteTest = useMutation({ mutationFn: testQuoteSource })
  const aiTest = useMutation({ mutationFn: testAiConnection })
  const notifyTest = useMutation({ mutationFn: testNotify })

  if (settings.isLoading) {
    return (
      <div className="col-span-12 flex h-40 items-center justify-center text-text-muted">
        <Loader2 className="size-5 animate-spin" />
      </div>
    )
  }
  const items = settings.data ?? []
  const dirty = Object.keys(draft).length > 0

  const valueOf = (item: SettingItem) => (item.key in draft ? draft[item.key] : item.value)

  const renderControl = (item: SettingItem) => {
    const value = valueOf(item)
    // 布尔 → 开关
    if (item.type === 'bool') {
      return (
        <Switch
          checked={Boolean(value)}
          onChange={(next) => setDraft((d) => ({ ...d, [item.key]: next }))}
        />
      )
    }
    // 枚举 → 下拉
    const options = SELECT_OPTIONS[item.key]
    if (options) {
      return (
        <select
          value={String(value)}
          onChange={(e) => setDraft((d) => ({ ...d, [item.key]: e.target.value }))}
          className="h-9 w-44 cursor-pointer rounded-[10px] border bg-background px-2.5 text-[13px] focus:border-ring focus:outline-none"
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )
    }
    // 数字 → 数字框
    if (item.type === 'int' || item.type === 'float') {
      return (
        <Input
          type="number"
          step={item.type === 'float' ? '0.1' : '1'}
          className="w-44 font-data"
          value={String(value)}
          onChange={(e) =>
            setDraft((d) => ({
              ...d,
              [item.key]:
                item.type === 'int'
                  ? Number.parseInt(e.target.value || '0', 10)
                  : Number(e.target.value || 0),
            }))
          }
        />
      )
    }
    // 敏感项 → 密码框
    if (item.secret) {
      return (
        <Input
          type="password"
          className="w-72 font-data"
          placeholder="未配置"
          value={String(value)}
          onChange={(e) => setDraft((d) => ({ ...d, [item.key]: e.target.value }))}
        />
      )
    }
    // 默认 → 文本框
    return (
      <Input
        className="w-72 font-data"
        value={String(value)}
        onChange={(e) => setDraft((d) => ({ ...d, [item.key]: e.target.value }))}
      />
    )
  }

  return (
    <>
      {GROUPS.map((group) => {
        const groupItems = items.filter((i) => i.key.startsWith(group.prefix))
        return (
          <section
            key={group.prefix}
            className="col-span-12 rounded-card border bg-card p-5 lg:col-span-4"
          >
            <header className="mb-1 flex items-center justify-between">
              <h3 className="text-sm font-semibold">{group.title}</h3>
              {group.prefix === 'quotes.' && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={quoteTest.isPending}
                  onClick={() => quoteTest.mutate()}
                >
                  {quoteTest.isPending ? <Loader2 className="size-3.5 animate-spin" /> : '测试连通'}
                </Button>
              )}
              {group.prefix === 'ai.' && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={aiTest.isPending}
                  onClick={() => aiTest.mutate()}
                  title="先保存密钥再测试（按当前已保存的配置发起一次最小对话）"
                >
                  {aiTest.isPending ? <Loader2 className="size-3.5 animate-spin" /> : '测试连接'}
                </Button>
              )}
              {group.prefix === 'notify.' && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={notifyTest.isPending}
                  onClick={() => notifyTest.mutate()}
                  title="先保存配置再测试（向所有已配置通道各发一条测试消息）"
                >
                  {notifyTest.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    '测试推送'
                  )}
                </Button>
              )}
            </header>
            <p className="mb-4 text-xs text-text-muted">{group.desc}</p>

            <div className="space-y-4">
              {groupItems.map((item) => (
                <div key={item.key} className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-[13px]">{item.label}</div>
                    <div className="mt-0.5 text-xs leading-5 text-text-muted">{item.hint}</div>
                  </div>
                  <div className="shrink-0">{renderControl(item)}</div>
                </div>
              ))}
            </div>

            {/* 报价源测试结果 */}
            {group.prefix === 'quotes.' && quoteTest.data && (
              <div className="mt-4 rounded-[10px] border bg-background p-3 font-data text-xs">
                {quoteTest.data.map((q) => (
                  <div key={q.symbol} className="flex justify-between py-0.5">
                    <span>
                      {q.name} {q.symbol}
                    </span>
                    <span className={q.pct_change >= 0 ? 'text-up' : 'text-down'}>
                      {q.price.toFixed(2)}（{q.pct_change >= 0 ? '+' : ''}
                      {q.pct_change}%）
                    </span>
                  </div>
                ))}
                <div className="mt-1 text-text-muted">
                  行情时间 {quoteTest.data[0]?.ts} · 连通正常
                </div>
              </div>
            )}
            {group.prefix === 'quotes.' && quoteTest.isError && (
              <div className="mt-4 rounded-[10px] border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
                连通失败：{(quoteTest.error as ApiError).message}
              </div>
            )}

            {/* AI 连接测试结果 */}
            {group.prefix === 'ai.' && aiTest.data && (
              <div className="mt-4 rounded-[10px] border bg-background p-3 text-xs">
                <span className="text-down">连接正常</span>
                <span className="ml-2 text-text-muted">模型回复：{aiTest.data.reply}</span>
              </div>
            )}
            {group.prefix === 'ai.' && aiTest.isError && (
              <div className="mt-4 rounded-[10px] border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
                {(aiTest.error as ApiError).message}
              </div>
            )}

            {/* 推送测试结果：逐通道展示成功/失败 */}
            {group.prefix === 'notify.' && notifyTest.data && (
              <div className="mt-4 space-y-1 rounded-[10px] border bg-background p-3 text-xs">
                {notifyTest.data.results.length === 0 ? (
                  <span className="text-text-muted">
                    {notifyTest.data.message ?? '未配置任何推送通道'}
                  </span>
                ) : (
                  notifyTest.data.results.map((r) => (
                    <div key={r.channel} className="flex items-center justify-between">
                      <span>{CHANNEL_LABEL[r.channel] ?? r.channel}</span>
                      {r.ok ? (
                        <span className="text-down">发送成功</span>
                      ) : (
                        <span className="text-destructive">失败：{r.error.slice(0, 40)}</span>
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
            {group.prefix === 'notify.' && notifyTest.isError && (
              <div className="mt-4 rounded-[10px] border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
                {(notifyTest.error as ApiError).message}
              </div>
            )}
          </section>
        )
      })}

      {/* 底部保存条：有改动时浮现 */}
      {(dirty || savedFlash) && (
        <Portal>
        <div className="fixed bottom-6 left-1/2 z-40 flex -translate-x-1/2 items-center gap-3 rounded-full border bg-popover px-5 py-2.5 shadow-xl">
          {savedFlash ? (
            <span className="flex items-center gap-1.5 text-[13px] text-down">
              <CheckCircle2 className="size-4" /> 已保存并即时生效
            </span>
          ) : (
            <>
              <span className="text-[13px] text-muted-foreground">
                {Object.keys(draft).length} 项修改未保存
              </span>
              <Button size="sm" variant="ghost" onClick={() => setDraft({})}>
                放弃
              </Button>
              <Button
                size="sm"
                variant="primary"
                disabled={save.isPending}
                onClick={() => save.mutate()}
              >
                {save.isPending ? <Loader2 className="size-3.5 animate-spin" /> : '保存'}
              </Button>
            </>
          )}
          {save.isError && (
            <span className="text-xs text-destructive">{(save.error as ApiError).message}</span>
          )}
        </div>
        </Portal>
      )}
    </>
  )
}
