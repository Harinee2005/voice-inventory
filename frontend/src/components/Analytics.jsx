import { useEffect, useState } from 'react'
import { TrendingDown, DollarSign, BarChart3, RefreshCw, Layers, TrendingUp } from 'lucide-react'
import { getAnalytics } from '../services/api'

const CATEGORY_COLORS = {
  Dairy:             '#3b82f6',
  Vegetables:        '#22c55e',
  Meat:              '#ef4444',
  Seafood:           '#06b6d4',
  Frozen:            '#8b5cf6',
  Beverages:         '#a855f7',
  'Dry Goods':       '#f59e0b',
  Bakery:            '#f97316',
  Produce:           '#84cc16',
  'Cleaning Supplies': '#14b8a6',
  Unknown:           '#94a3b8',
}

function StatCard({ icon: Icon, label, value, valueClass = 'text-gray-900', subtitle, iconBg = 'bg-indigo-50', iconColor = 'text-indigo-500' }) {
  return (
    <div className="bg-white rounded-2xl border border-gray-200 p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-gray-400 text-xs uppercase tracking-wider mb-1">{label}</p>
          <p className={`text-3xl font-bold ${valueClass}`}>{value}</p>
          {subtitle && <p className="text-gray-400 text-xs mt-0.5">{subtitle}</p>}
        </div>
        <div className={`p-2.5 rounded-xl ${iconBg}`}>
          <Icon className={`w-5 h-5 ${iconColor}`} />
        </div>
      </div>
    </div>
  )
}

export default function Analytics({ refreshTrigger }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      const { data: res } = await getAnalytics()
      setData(res)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [refreshTrigger])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40 text-gray-400">
        <RefreshCw className="w-5 h-5 animate-spin mr-2" /> Loading analytics...
      </div>
    )
  }

  if (!data) return null

  const maxCat = data.category_summary.reduce((m, c) => Math.max(m, c.item_count), 1)
  const pa = data.price_analytics || {}
  const topItems = pa.top_items || []
  const maxItemValue = topItems.reduce((m, i) => Math.max(m, i.total_value), 1)

  return (
    <div className="space-y-5">
      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard
          icon={Layers}
          label="Total Items"
          value={data.total_items}
          subtitle={`${data.total_categories} categories`}
        />
        <StatCard
          icon={TrendingDown}
          label="Low Stock"
          value={data.low_stock_items.length}
          valueClass={data.low_stock_items.length > 0 ? 'text-red-500' : 'text-emerald-600'}
          subtitle="≤ 5 units"
          iconBg={data.low_stock_items.length > 0 ? 'bg-red-50' : 'bg-emerald-50'}
          iconColor={data.low_stock_items.length > 0 ? 'text-red-400' : 'text-emerald-500'}
        />
        <StatCard
          icon={DollarSign}
          label="Total Value"
          value={pa.total_value != null
            ? `$${pa.total_value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
            : '—'}
          valueClass="text-emerald-600"
          subtitle="inventory worth"
          iconBg="bg-emerald-50"
          iconColor="text-emerald-500"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Category breakdown */}
        <div className="bg-white rounded-2xl border border-gray-200 p-5">
          <div className="flex items-center gap-2 mb-5">
            <BarChart3 className="w-4 h-4 text-indigo-500" />
            <span className="text-gray-800 font-semibold text-sm">Category Breakdown</span>
          </div>
          {data.category_summary.length === 0 ? (
            <p className="text-gray-400 text-sm text-center py-4">No data yet</p>
          ) : (
            <div className="space-y-3">
              {data.category_summary.map((cat) => (
                <div key={cat.category}>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-gray-600 text-xs font-medium">{cat.category}</span>
                    <span className="text-gray-400 text-xs">{cat.item_count} items</span>
                  </div>
                  <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${(cat.item_count / maxCat) * 100}%`,
                        background: CATEGORY_COLORS[cat.category] || '#94a3b8',
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Price Analysis */}
        <div className="bg-white rounded-2xl border border-gray-200 p-5">
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-emerald-500" />
              <span className="text-gray-800 font-semibold text-sm">Price Analysis</span>
            </div>
            {pa.total_value > 0 && (
              <span className="text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-600 font-medium border border-emerald-100">
                ${pa.total_value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} total
              </span>
            )}
          </div>
          {topItems.length === 0 ? (
            <p className="text-gray-400 text-sm text-center py-4">No pricing data yet — ARIA estimates prices as you add items.</p>
          ) : (
            <div className="space-y-3">
              {topItems.map((item) => (
                <div key={item.item_name}>
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-gray-700 text-xs font-medium capitalize truncate">{item.item_name}</span>
                      <span className="text-gray-400 text-xs shrink-0">{item.quantity} {item.unit} × ${item.unit_price}</span>
                    </div>
                    <span className="text-emerald-600 text-xs font-bold shrink-0 ml-2">
                      ${item.total_value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-emerald-400 to-teal-500 transition-all duration-500"
                      style={{ width: `${(item.total_value / maxItemValue) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Low stock */}
      {data.low_stock_items.length > 0 && (
        <div className="bg-white rounded-2xl border border-red-100 p-5">
          <div className="flex items-center gap-2 mb-4">
            <TrendingDown className="w-4 h-4 text-red-400" />
            <span className="text-gray-800 font-semibold text-sm">Low Stock Alert</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {data.low_stock_items.map((item) => (
              <div
                key={item.id}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-red-50 border border-red-100"
              >
                <span className="text-gray-700 text-xs capitalize">{item.item_name}</span>
                <span className="text-red-500 text-xs font-semibold">{item.quantity} {item.unit}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
