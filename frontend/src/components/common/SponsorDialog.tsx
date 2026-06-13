import { AnimatePresence, motion } from 'motion/react'
import { Coffee, ExternalLink, Heart, Star, X } from 'lucide-react'

import { Portal } from '@/components/common/Portal'
import { cn } from '@/lib/utils'

/** 项目仓库地址（赞助/关于统一引用） */
export const REPO_URL = 'https://github.com/97Gz/StockNova'

/** 两个收款码（放在 frontend/public/sponsor 下，构建时原样拷贝到站点根） */
const QR_LIST = [
  { src: '/sponsor/wechat.png', label: '微信', accent: 'text-up' },
  { src: '/sponsor/alipay.jpg', label: '支付宝', accent: 'text-[#1677ff]' },
]

/**
 * 赞助弹窗：展示微信/支付宝收款码 + 一句真诚的感谢。
 * 复用于侧边栏「请作者喝咖啡」入口与设置中心赞助卡。
 */
export function SponsorDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <AnimatePresence>
      {open && (
        <Portal>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm"
            onClick={onClose}
          >
            <motion.div
              initial={{ scale: 0.95, y: 12 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.95, y: 12 }}
              onClick={(e) => e.stopPropagation()}
              className="w-full max-w-md rounded-card border bg-card p-6 shadow-2xl"
            >
              <div className="mb-1 flex items-center justify-between">
                <h3 className="flex items-center gap-2 text-base font-semibold">
                  <Coffee className="size-4 text-gold" /> 请作者喝杯咖啡
                </h3>
                <button
                  onClick={onClose}
                  className="flex size-7 items-center justify-center rounded-control border text-muted-foreground transition-colors hover:text-foreground"
                >
                  <X className="size-3.5" />
                </button>
              </div>
              <p className="mb-5 text-xs leading-relaxed text-text-muted">
                StockNova 是我用业余时间做的开源项目，免费且无任何付费功能。
                如果它帮你省了时间、看清了行情，欢迎扫码请我喝杯咖啡——
                这会让我更有动力把它做得更好。不打赏也完全没关系，点个 Star 同样是支持 🌟
              </p>

              <div className="grid grid-cols-2 gap-4">
                {QR_LIST.map((q) => (
                  <div key={q.label} className="flex flex-col items-center gap-2">
                    <div className="rounded-card border bg-white p-2">
                      <img src={q.src} alt={`${q.label}收款码`} className="size-40 object-contain" />
                    </div>
                    <span className={cn('text-xs font-medium', q.accent)}>{q.label}</span>
                  </div>
                ))}
              </div>

              {/* 关注公众号：不打赏也能用这种方式支持作者、获取更新 */}
              <div className="mt-5 flex items-center gap-4 rounded-card border bg-muted/30 p-3">
                <div className="shrink-0 rounded-card border bg-white p-1.5">
                  <img src="/sponsor/mp-qr.jpg" alt="微信公众号二维码" className="size-24 object-contain" />
                </div>
                <div className="text-xs leading-relaxed text-text-muted">
                  <div className="mb-1 font-medium text-foreground">扫码关注作者公众号</div>
                  第一时间获取新版本动态、使用技巧，也欢迎来交流反馈。
                </div>
              </div>

              <a
                href={REPO_URL}
                target="_blank"
                rel="noreferrer"
                className="mt-5 flex w-full items-center justify-center gap-1.5 rounded-control border py-2 text-xs text-muted-foreground transition-colors hover:border-gold/40 hover:text-gold"
              >
                <Star className="size-3.5" /> 给项目点个 Star
              </a>
            </motion.div>
          </motion.div>
        </Portal>
      )}
    </AnimatePresence>
  )
}

/**
 * 赞助/关于卡片：嵌入设置中心。展示项目简介 + 收款码 + 仓库链接。
 */
export function SponsorCard() {
  return (
    <section className="col-span-12 rounded-card border bg-card p-5">
      <header className="mb-3 flex items-center gap-2">
        <Heart className="size-4 text-gold" />
        <h3 className="text-sm font-semibold">关于 · 赞助</h3>
      </header>
      <p className="mb-4 text-xs leading-relaxed text-text-muted">
        StockNova（星智股）是一款开源的 A 股智能分析终端，集行情、策略、回测、多角色 AI
        投研于一体，完全免费、本地运行、数据自持。如果它对你有帮助，欢迎扫码赞助或在 GitHub
        点个 Star ⭐——你的支持是项目持续迭代的动力。
      </p>
      <div className="flex flex-wrap items-center gap-5">
        {QR_LIST.map((q) => (
          <div key={q.label} className="flex flex-col items-center gap-1.5">
            <div className="rounded-card border bg-white p-1.5">
              <img src={q.src} alt={`${q.label}收款码`} className="size-28 object-contain" />
            </div>
            <span className={cn('text-[11px]', q.accent)}>{q.label}赞助</span>
          </div>
        ))}
        <div className="flex flex-col items-center gap-1.5">
          <div className="rounded-card border bg-white p-1.5">
            <img src="/sponsor/mp-qr.jpg" alt="微信公众号二维码" className="size-28 object-contain" />
          </div>
          <span className="text-[11px] text-up">关注公众号</span>
        </div>
        <a
          href={REPO_URL}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1.5 rounded-control border border-gold/40 px-3.5 py-2 text-xs text-gold transition-colors hover:bg-gold/10"
        >
          <ExternalLink className="size-3.5" /> GitHub 仓库
        </a>
      </div>
    </section>
  )
}
