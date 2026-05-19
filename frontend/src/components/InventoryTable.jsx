import { useState, useEffect } from 'react'
import { Package, Search, AlertTriangle, RefreshCw, Calendar, ChevronLeft, ChevronRight, MapPin, Globe } from 'lucide-react'
import { getInventory, getAvailableDates } from '../services/api'
import { formatDistanceToNow, format } from 'date-fns'

const utc = (ts) => ts ? new Date(ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z') : null

const CATEGORY_COLORS = {
  Dairy:              'bg-blue-50 text-blue-600 border-blue-100',
  Vegetables:         'bg-green-50 text-green-600 border-green-100',
  Meat:               'bg-red-50 text-red-600 border-red-100',
  Seafood:            'bg-cyan-50 text-cyan-600 border-cyan-100',
  Frozen:             'bg-indigo-50 text-indigo-600 border-indigo-100',
  Beverages:          'bg-purple-50 text-purple-600 border-purple-100',
  'Dry Goods':        'bg-amber-50 text-amber-600 border-amber-100',
  Bakery:             'bg-orange-50 text-orange-600 border-orange-100',
  Produce:            'bg-lime-50 text-lime-600 border-lime-100',
  'Cleaning Supplies':'bg-teal-50 text-teal-600 border-teal-100',
  Unknown:            'bg-gray-50 text-gray-500 border-gray-200',
}

function todayStr() { return format(new Date(), 'yyyy-MM-dd') }

export default function InventoryTable({ refreshTrigger, workspace }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [viewAll, setViewAll] = useState(false)
  const [selectedDate, setSelectedDate] = useState(todayStr())
  const [availableDates, setAvailableDates] = useState([])

  useEffect(() => {
    getAvailableDates().then(({ data }) => {
      const dates = data.dates || []
      if (!dates.includes(todayStr())) dates.unshift(todayStr())
      setAvailableDates(dates)
    }).catch(() => setAvailableDates([todayStr()]))
  }, [refreshTrigger])

  const load = async (date) => {
    setLoading(true)
    try {
      const params = { date: date || selectedDate }
      if (categoryFilter) params.category = categoryFilter
      if (!viewAll && workspace) {
        params.location_name = workspace.locationName
        params.storage_area = workspace.storageAreaName
      }
      const { data } = await getInventory(params)
      setItems(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(selectedDate) }, [refreshTrigger, selectedDate, categoryFilter, viewAll, workspace])

  const navigateDate = (dir) => {
    const idx = availableDates.indexOf(selectedDate)
    const next = idx + dir
    if (next >= 0 && next < availableDates.length) setSelectedDate(availableDates[next])
  }

  const isToday = selectedDate === todayStr()
  const categories = [...new Set(items.map((i) => i.category))].sort()
  const filtered = items.filter((i) => i.item_name.toLowerCase().includes(search.toLowerCase()))
  const totalValue = filtered.reduce((sum, i) => sum + (i.unit_price && i.quantity ? i.unit_price * i.quantity : 0), 0)

  return (
    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden flex flex-col h-full">
      {/* Header */}
      <div className="px-5 py-4 border-b border-gray-100 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 mr-auto">
          <Package className="w-4 h-4 text-indigo-500" />
          <span className="text-gray-800 font-semibold text-sm">Live Inventory</span>
          <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-600 font-medium border border-indigo-100">
            {filtered.length} items
          </span>
          {totalValue > 0 && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium border border-emerald-100">
              ${totalValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} total
            </span>
          )}
        </div>

        {/* Date navigator */}
        <div className="flex items-center gap-1 px-2 py-1.5 rounded-xl bg-gray-50 border border-gray-200">
          <button
            onClick={() => navigateDate(1)}
            disabled={availableDates.indexOf(selectedDate) >= availableDates.length - 1}
            className="p-1 rounded text-gray-400 hover:text-gray-600 disabled:opacity-30 transition-colors"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>
          <div className="flex items-center gap-1.5 px-2">
            <Calendar className="w-3.5 h-3.5 text-indigo-400" />
            <input
              type="date"
              value={selectedDate}
              onChange={(e) => setSelectedDate(e.target.value)}
              className="bg-transparent text-gray-700 text-xs outline-none w-28 cursor-pointer"
            />
            {isToday && (
              <span className="text-xs px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-100">
                Today
              </span>
            )}
          </div>
          <button
            onClick={() => navigateDate(-1)}
            disabled={availableDates.indexOf(selectedDate) <= 0}
            className="p-1 rounded text-gray-400 hover:text-gray-600 disabled:opacity-30 transition-colors"
          >
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Search */}
        <div className="relative">
          <Search className="w-3.5 h-3.5 text-gray-300 absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search items..."
            className="pl-8 pr-3 py-1.5 rounded-lg bg-gray-50 border border-gray-200 text-gray-700 text-xs w-36 outline-none focus:border-indigo-300"
          />
        </div>

        {/* Category filter */}
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="px-2 py-1.5 rounded-lg bg-gray-50 border border-gray-200 text-gray-600 text-xs outline-none focus:border-indigo-300"
        >
          <option value="">All Categories</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>

        {/* View All toggle */}
        {workspace && (
          <button
            onClick={() => setViewAll((v) => !v)}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs border transition-all duration-200 font-medium ${
              viewAll
                ? 'bg-indigo-50 text-indigo-600 border-indigo-200'
                : 'bg-gray-50 text-gray-500 border-gray-200 hover:text-gray-700'
            }`}
          >
            {viewAll ? <Globe className="w-3 h-3" /> : <MapPin className="w-3 h-3" />}
            {viewAll ? 'All Locations' : 'My Area'}
          </button>
        )}

        <button
          onClick={() => load(selectedDate)}
          className="p-1.5 rounded-lg bg-gray-50 border border-gray-200 text-gray-400 hover:text-gray-600 transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Workspace banner */}
      {workspace && !viewAll && (
        <div className="px-5 py-2 bg-indigo-50/60 border-b border-indigo-100 flex items-center gap-2">
          <MapPin className="w-3.5 h-3.5 text-indigo-400 shrink-0" />
          <span className="text-indigo-500 text-xs">
            <span className="text-gray-400">{workspace.locationName}</span>
            <span className="text-gray-300 mx-1.5">›</span>
            <span className="font-semibold text-indigo-600">{workspace.storageAreaName}</span>
          </span>
          <button
            onClick={() => setViewAll(true)}
            className="ml-auto text-xs px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-600 hover:bg-indigo-200 transition-colors font-medium"
          >
            View All Locations
          </button>
        </div>
      )}

      {/* Past-date banner */}
      {!isToday && (
        <div className="px-5 py-2 bg-amber-50 border-b border-amber-100 flex items-center gap-2">
          <Calendar className="w-3.5 h-3.5 text-amber-500" />
          <span className="text-amber-700 text-xs">
            Viewing historical counts for <strong>{selectedDate}</strong> — read-only
          </span>
          <button
            onClick={() => setSelectedDate(todayStr())}
            className="ml-auto text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 hover:bg-amber-200 transition-colors"
          >
            Go to Today
          </button>
        </div>
      )}

      {/* Table */}
      <div className="overflow-auto flex-1">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50/60">
              {['Item', 'Category', 'Quantity', 'Unit Price', 'Total Value', 'Location', 'Storage Area', 'Worker', 'Updated'].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-gray-400 font-medium text-xs uppercase tracking-wider whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className="text-center py-10 text-gray-400 text-sm">Loading...</td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={9} className="text-center py-12">
                  <div className="flex flex-col items-center gap-2 text-gray-400">
                    <Package className="w-8 h-8 opacity-30" />
                    <span className="text-sm">
                      {workspace && !viewAll
                        ? `No items in ${workspace.storageAreaName} (${workspace.locationName}) for ${selectedDate}`
                        : `No items for ${selectedDate}`}
                    </span>
                    {workspace && !viewAll && (
                      <button onClick={() => setViewAll(true)} className="text-xs text-indigo-500 hover:text-indigo-700 underline">
                        View all locations
                      </button>
                    )}
                    {!isToday && (
                      <button onClick={() => setSelectedDate(todayStr())} className="text-xs text-indigo-500 hover:text-indigo-700 underline">
                        Switch to today
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ) : (
              filtered.map((item) => (
                <tr
                  key={item.id}
                  className={`border-b border-gray-50 hover:bg-gray-50/60 transition-colors ${item.is_flagged ? 'flag-pulse' : ''}`}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      {item.is_flagged && <AlertTriangle className="w-3.5 h-3.5 text-red-400 shrink-0" />}
                      <span className="text-gray-800 capitalize font-medium text-xs">{item.item_name}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${CATEGORY_COLORS[item.category] || CATEGORY_COLORS.Unknown}`}>
                      {item.category}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`font-mono font-bold text-xs ${item.quantity <= 5 ? 'text-red-500' : item.quantity <= 15 ? 'text-amber-500' : 'text-emerald-600'}`}>
                      {item.quantity}
                    </span>
                    <span className="text-gray-400 text-xs ml-1">{item.unit}</span>
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs font-mono">
                    {item.unit_price != null ? `$${item.unit_price.toFixed(2)}` : <span className="text-gray-200">—</span>}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">
                    {item.unit_price != null && item.quantity != null
                      ? <span className="text-emerald-600 font-bold">${(item.unit_price * item.quantity).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                      : <span className="text-gray-200">—</span>}
                  </td>
                  <td className="px-4 py-3 text-gray-400 text-xs">{item.location_name || '—'}</td>
                  <td className="px-4 py-3 text-gray-500 text-xs">{item.storage_area}</td>
                  <td className="px-4 py-3 text-gray-400 text-xs">{item.updated_by}</td>
                  <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">
                    {item.timestamp ? formatDistanceToNow(utc(item.timestamp), { addSuffix: true }) : '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
