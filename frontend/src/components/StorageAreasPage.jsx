import { useEffect, useState } from 'react'
import { Grid3X3, Plus, Trash2, Loader, MapPin } from 'lucide-react'
import { getLocations, getStorageAreas, createStorageArea, deleteStorageArea, getInventory } from '../services/api'

export default function StorageAreasPage({ refreshTrigger }) {
  const [locations, setLocations] = useState([])
  const [areas, setAreas] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(null) // locationId
  const [newAreaName, setNewAreaName] = useState('')
  const [adding, setAdding] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const { data: locs } = await getLocations()
      setLocations(locs)
      const allAreas = await Promise.all(
        locs.map(async (loc) => {
          const { data } = await getStorageAreas(loc.id)
          const withStats = await Promise.all(
            (data || []).map(async (area) => {
              try {
                const { data: items } = await getInventory({ location_name: loc.name, storage_area: area.name })
                return { ...area, locationName: loc.name, locationId: loc.id, skuCount: items.length }
              } catch {
                return { ...area, locationName: loc.name, locationId: loc.id, skuCount: 0 }
              }
            })
          )
          return withStats
        })
      )
      setAreas(allAreas.flat())
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [refreshTrigger])

  const handleAdd = async () => {
    if (!newAreaName.trim() || !showAdd) return
    setAdding(true)
    try {
      const { data } = await createStorageArea(showAdd, { name: newAreaName.trim() })
      const loc = locations.find((l) => l.id === showAdd)
      setAreas((prev) => [...prev, { ...data, locationName: loc?.name || '', locationId: showAdd, skuCount: 0 }])
      setNewAreaName('')
      setShowAdd(null)
    } finally {
      setAdding(false)
    }
  }

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this storage area?')) return
    await deleteStorageArea(id)
    setAreas((prev) => prev.filter((a) => a.id !== id))
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40 text-gray-400">
        <Loader className="w-5 h-5 animate-spin mr-2" /> Loading storage areas...
      </div>
    )
  }

  return (
    <div>
      {locations.length === 0 ? (
        <div className="bg-white rounded-2xl border border-dashed border-gray-300 p-12 text-center">
          <p className="text-gray-400 text-sm">No locations yet — create locations first.</p>
        </div>
      ) : (
        locations.map((loc) => {
          const locAreas = areas.filter((a) => a.locationId === loc.id)
          return (
            <div key={loc.id} className="mb-6">
              <div className="flex items-center gap-2 mb-3">
                <MapPin className="w-4 h-4 text-indigo-500" />
                <h3 className="text-gray-700 font-semibold text-sm">{loc.name}</h3>
                <span className="text-gray-300 text-xs ml-auto">
                  {locAreas.length} area{locAreas.length !== 1 ? 's' : ''}
                </span>
                <button
                  onClick={() => { setShowAdd(loc.id); setNewAreaName('') }}
                  className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-700 font-medium"
                >
                  <Plus className="w-3.5 h-3.5" /> Add Area
                </button>
              </div>

              {showAdd === loc.id && (
                <div className="flex gap-2 mb-3">
                  <input
                    autoFocus
                    value={newAreaName}
                    onChange={(e) => setNewAreaName(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
                    placeholder="e.g. Walk-in Fridge, Pantry"
                    className="flex-1 px-3 py-2 rounded-lg border border-gray-200 text-gray-800 text-sm outline-none focus:border-indigo-400"
                  />
                  <button
                    onClick={handleAdd}
                    disabled={!newAreaName.trim() || adding}
                    className="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm disabled:opacity-50"
                  >
                    {adding ? <Loader className="w-4 h-4 animate-spin" /> : 'Add'}
                  </button>
                  <button
                    onClick={() => setShowAdd(null)}
                    className="px-3 py-2 rounded-lg border border-gray-200 text-gray-500 text-sm"
                  >
                    Cancel
                  </button>
                </div>
              )}

              {locAreas.length === 0 ? (
                <div className="bg-white rounded-xl border border-dashed border-gray-200 p-6 text-center text-gray-400 text-sm">
                  No storage areas — add one above
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                  {locAreas.map((area) => {
                    const load = Math.min(100, (area.skuCount / 10) * 100)
                    const overCap = load >= 100
                    return (
                      <div key={area.id} className="bg-white rounded-xl border border-gray-200 p-4 hover:shadow-sm transition-shadow">
                        <div className="flex items-start justify-between mb-3">
                          <div className="w-9 h-9 rounded-lg bg-indigo-50 flex items-center justify-center">
                            <Grid3X3 className="w-4.5 h-4.5 text-indigo-500" />
                          </div>
                          <div className="flex items-center gap-1">
                            <span className={`text-xs font-semibold ${overCap ? 'text-red-500' : 'text-gray-400'}`}>
                              {Math.round(load)}% CAP
                            </span>
                            <button
                              onClick={() => handleDelete(area.id)}
                              className="p-1 text-gray-300 hover:text-red-400 transition-colors"
                            >
                              <Trash2 className="w-3 h-3" />
                            </button>
                          </div>
                        </div>
                        <p className="text-gray-800 font-semibold text-sm">{area.name}</p>
                        <p className="text-gray-400 text-xs mt-0.5 mb-3">{loc.name}</p>
                        <div className="mb-1 flex items-center justify-between">
                          <span className="text-gray-400 text-xs">Storage Load</span>
                          <span className="text-gray-600 text-xs font-medium">{area.skuCount} SKUs</span>
                        </div>
                        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all duration-500 ${overCap ? 'bg-red-400' : 'bg-indigo-500'}`}
                            style={{ width: `${Math.max(4, load)}%` }}
                          />
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })
      )}
    </div>
  )
}
