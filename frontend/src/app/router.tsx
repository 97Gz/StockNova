import { createBrowserRouter } from 'react-router'

import { AppLayout } from '@/app/AppLayout'
import { BacktestPage } from '@/features/backtest/BacktestPage'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { BoardDetailPage } from '@/features/market/BoardDetailPage'
import { NewsPage } from '@/features/news/NewsPage'
import { PortfolioPage } from '@/features/portfolio/PortfolioPage'
import { ResearchPage } from '@/features/research/ResearchPage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { StockDetailPage } from '@/features/stock/StockDetailPage'
import { SignalsPage } from '@/features/strategy/SignalsPage'
import { StrategyMarketPage } from '@/features/strategy/StrategyMarketPage'
import { WatchlistPage } from '@/features/watchlist/WatchlistPage'

/**
 * 路由表：8 个一级页面 + 个股详情（/stock/:symbol，经搜索/列表进入）。
 */
export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'strategies', element: <StrategyMarketPage /> },
      { path: 'signals', element: <SignalsPage /> },
      { path: 'watchlist', element: <WatchlistPage /> },
      { path: 'portfolio', element: <PortfolioPage /> },
      { path: 'research', element: <ResearchPage /> },
      { path: 'backtest', element: <BacktestPage /> },
      { path: 'news', element: <NewsPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'stock/:symbol', element: <StockDetailPage /> },
      { path: 'board/:code', element: <BoardDetailPage /> },
    ],
  },
])
