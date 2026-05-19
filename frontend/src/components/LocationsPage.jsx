import { useEffect, useState } from 'react'
import { MapPin, Plus, Trash2, Loader, Package } from 'lucide-react'
import { getLocations, createLocation, deleteLocation, getStorageAreas, getInventory } from '../services/api'

export default function LocationsPage({ refreshTrigger }) {
  const [locations, setLocations] = useState([])
  const [stats, setStats] = useState({})
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [newName, setNewName] = useState('')
  const [adding, setAdding] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const { data } = await getLocations()
      setLocations(data)
      // fetch stats per location in parallel
      const statEntries = await Promise.all(
        data.map(async (loc) => {
          try {
            const [areasRes, invRes] = await Promise.all([
              getStorageAreas(loc.id),
              getInventory({ location_name: loc.name }),
            ])
            const items = invRes.data || []
            const totalValue = items.reduce(
              (sum, i) => sum + (i.unit_price && i.quantity ? i.unit_price * i.quantity : 0), 0
            )
            return [loc.id, { areas: areasRes.data?.length || 0, skus: items.length, totalValue }]
          } catch {
            return [loc.id, { areas: 0, skus: 0, totalValue: 0 }]
          }
        })
      )
      setStats(Object.fromEntries(statEntries))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [refreshTrigger])

  const handleAdd = async () => {
    if (!newName.trim()) return
    setAdding(true)
    try {
      const { data } = await createLocation({ name: newName.trim() })
      setLocations((prev) => [...prev, data])
      setStats((prev) => ({ ...prev, [data.id]: { areas: 0, skus: 0, totalValue: 0 } }))
      setNewName('')
      setShowAdd(false)
    } finally {
      setAdding(false)
    }
  }

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this location and all its storage areas?')) return
    await deleteLocation(id)
    setLocations((prev) => prev.filter((l) => l.id !== id))
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40 text-gray-400">
        <Loader className="w-5 h-5 animate-spin mr-2" /> Loading locations...
      </div>
    )
  }

  return (
    <div>
      <div className="flex justify-end mb-5">
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium transition-colors"
        >
          <Plus className="w-4 h-4" /> Add Location
        </button>
      </div>

      {showAdd && (
        <div className="bg-white rounded-2xl border border-gray-200 p-5 mb-4 flex gap-3">
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
            placeholder="e.g. New York, California, Texas"
            className="flex-1 px-3 py-2 rounded-lg border border-gray-200 text-gray-800 text-sm outline-none focus:border-indigo-400"
          />
          <button
            onClick={handleAdd}
            disabled={!newName.trim() || adding}
            className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium disabled:opacity-50 transition-colors"
          >
            {adding ? <Loader className="w-4 h-4 animate-spin" /> : 'Add'}
          </button>
          <button
            onClick={() => { setShowAdd(false); setNewName('') }}
            className="px-4 py-2 rounded-lg border border-gray-200 text-gray-500 text-sm hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
        </div>
      )}

      {locations.length === 0 ? (
        <div className="bg-white rounded-2xl border border-dashed border-gray-300 p-12 text-center">
          <MapPin className="w-10 h-10 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500 text-sm">No locations yet — add your first restaurant branch above.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {locations.map((loc) => {
            const s = stats[loc.id] || {}
            return (
              <div key={loc.id} className="bg-white rounded-2xl border border-gray-200 p-5 hover:shadow-md transition-shadow">
                <div className="flex items-start justify-between mb-4">
                  <div className="w-10 h-10 rounded-xl bg-indigo-50 flex items-center justify-center">
                    <MapPin className="w-5 h-5 text-indigo-500" />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium border border-emerald-200">
                      Active
                    </span>
                    <button
                      onClick={() => handleDelete(loc.id)}
                      className="p-1 rounded text-gray-300 hover:text-red-400 transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
                <h3 className="text-gray-900 font-bold text-base mb-1">{loc.name}</h3>
                {loc.description && (
                  <p className="text-gray-400 text-xs mb-3">{loc.description}</p>
                )}
                <div className="flex gap-4 pt-3 border-t border-gray-100 mt-3">
                  <div>
                    <p className="text-gray-400 text-xs uppercase tracking-wide">Storage Areas</p>
                    <p className="text-gray-800 font-semibold text-sm">{s.areas ?? '—'}</p>
                  </div>
                  <div>
                    <p className="text-gray-400 text-xs uppercase tracking-wide">SKU Count</p>
                    <p className="text-gray-800 font-semibold text-sm">{s.skus ?? '—'}</p>
                  </div>
                  <div>
                    <p className="text-gray-400 text-xs uppercase tracking-wide">Est. Value</p>
                    <p className="text-indigo-600 font-semibold text-sm">
                      {s.totalValue > 0
                        ? `$${s.totalValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                        : '—'}
                    </p>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
