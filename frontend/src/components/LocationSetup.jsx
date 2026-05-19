import { useState, useEffect, useCallback } from 'react'
import { MapPin, Package, Plus, ChevronRight, ArrowLeft, Loader, Check } from 'lucide-react'
import { getLocations, createLocation, getStorageAreas, createStorageArea } from '../services/api'

export default function LocationSetup({ onComplete, onCancel }) {
  const [step, setStep] = useState(1)
  const [locations, setLocations] = useState([])
  const [selectedLocation, setSelectedLocation] = useState(null)
  const [storageAreas, setStorageAreas] = useState([])
  const [selectedArea, setSelectedArea] = useState(null)
  const [loading, setLoading] = useState(true)
  const [areasLoading, setAreasLoading] = useState(false)

  const [showAddLocation, setShowAddLocation] = useState(false)
  const [newLocationName, setNewLocationName] = useState('')
  const [addingLocation, setAddingLocation] = useState(false)

  const [showAddArea, setShowAddArea] = useState(false)
  const [newAreaName, setNewAreaName] = useState('')
  const [addingArea, setAddingArea] = useState(false)

  useEffect(() => {
    getLocations().then(({ data }) => setLocations(data)).finally(() => setLoading(false))
  }, [])

  const handleSelectLocation = useCallback(async (loc) => {
    setSelectedLocation(loc)
    setSelectedArea(null)
    setStorageAreas([])
    setAreasLoading(true)
    try {
      const { data } = await getStorageAreas(loc.id)
      setStorageAreas(data)
    } finally {
      setAreasLoading(false)
    }
  }, [])

  const handleNextStep = () => {
    if (!selectedLocation) return
    setStep(2)
  }

  const handleBack = () => {
    setStep(1)
    setSelectedArea(null)
  }

  const handleAddLocation = async () => {
    if (!newLocationName.trim()) return
    setAddingLocation(true)
    try {
      const { data } = await createLocation({ name: newLocationName.trim() })
      setLocations((prev) => [...prev, data])
      setNewLocationName('')
      setShowAddLocation(false)
      handleSelectLocation(data)
    } finally {
      setAddingLocation(false)
    }
  }

  const handleAddArea = async () => {
    if (!newAreaName.trim() || !selectedLocation) return
    setAddingArea(true)
    try {
      const { data } = await createStorageArea(selectedLocation.id, { name: newAreaName.trim() })
      setStorageAreas((prev) => [...prev, data])
      setNewAreaName('')
      setShowAddArea(false)
      setSelectedArea(data)
    } finally {
      setAddingArea(false)
    }
  }

  const handleConfirm = () => {
    if (!selectedLocation || !selectedArea) return
    onComplete({
      locationId: selectedLocation.id,
      locationName: selectedLocation.name,
      storageAreaId: selectedArea.id,
      storageAreaName: selectedArea.name,
    })
  }

  return (
    <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl w-full max-w-sm shadow-2xl overflow-hidden">

        {/* ── Step 1: Select Location ── */}
        {step === 1 && (
          <>
            <div className="px-6 pt-6 pb-4">
              <h2 className="text-xl font-bold text-gray-900">Select Location</h2>
              <p className="text-gray-400 text-sm mt-1">Please confirm which site you are auditing.</p>
            </div>

            <div className="px-3 pb-2 space-y-1 max-h-64 overflow-y-auto">
              {loading ? (
                <div className="flex justify-center py-8">
                  <Loader className="w-5 h-5 text-indigo-400 animate-spin" />
                </div>
              ) : (
                <>
                  {locations.map((loc) => {
                    const active = selectedLocation?.id === loc.id
                    return (
                      <button
                        key={loc.id}
                        onClick={() => handleSelectLocation(loc)}
                        className={`w-full flex items-center gap-3 px-4 py-3.5 rounded-xl border transition-all text-left ${
                          active
                            ? 'border-indigo-300 bg-indigo-50'
                            : 'border-gray-100 hover:bg-gray-50 hover:border-gray-200'
                        }`}
                      >
                        <div className={`w-9 h-9 rounded-full flex items-center justify-center shrink-0 ${active ? 'bg-indigo-600' : 'bg-gray-100'}`}>
                          <MapPin className={`w-4 h-4 ${active ? 'text-white' : 'text-gray-400'}`} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className={`font-semibold text-sm ${active ? 'text-indigo-700' : 'text-gray-800'}`}>{loc.name}</p>
                          {loc.description && <p className="text-gray-400 text-xs truncate">{loc.description}</p>}
                        </div>
                        {active && <Check className="w-4 h-4 text-indigo-500 shrink-0" />}
                      </button>
                    )
                  })}

                  {showAddLocation ? (
                    <div className="flex gap-2 px-1 pt-1">
                      <input
                        autoFocus
                        value={newLocationName}
                        onChange={(e) => setNewLocationName(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleAddLocation()}
                        placeholder="e.g. New York, California"
                        className="flex-1 px-3 py-2 rounded-lg border border-gray-200 text-gray-800 text-sm outline-none focus:border-indigo-400"
                      />
                      <button
                        onClick={handleAddLocation}
                        disabled={!newLocationName.trim() || addingLocation}
                        className="px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm disabled:opacity-50"
                      >
                        {addingLocation ? <Loader className="w-3.5 h-3.5 animate-spin" /> : 'Add'}
                      </button>
                      <button
                        onClick={() => { setShowAddLocation(false); setNewLocationName('') }}
                        className="px-3 py-2 rounded-lg border border-gray-200 text-gray-400 text-sm"
                      >
                        ✕
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setShowAddLocation(true)}
                      className="w-full flex items-center gap-3 px-4 py-3.5 rounded-xl border border-dashed border-gray-200 text-gray-400 hover:border-indigo-200 hover:text-indigo-400 transition-all text-sm"
                    >
                      <div className="w-9 h-9 rounded-full bg-gray-50 flex items-center justify-center shrink-0">
                        <Plus className="w-4 h-4" />
                      </div>
                      Add new location
                    </button>
                  )}
                </>
              )}
            </div>

            <div className="px-4 pb-5 pt-3 space-y-2">
              <button
                onClick={handleNextStep}
                disabled={!selectedLocation}
                className="w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-100 disabled:text-indigo-300 text-white font-semibold text-sm flex items-center justify-center gap-2 transition-all"
              >
                Next Step <ChevronRight className="w-4 h-4" />
              </button>
              {onCancel && (
                <button onClick={onCancel} className="w-full text-center text-gray-400 text-sm py-1 hover:text-gray-600 transition-colors">
                  Cancel
                </button>
              )}
            </div>
          </>
        )}

        {/* ── Step 2: Select Storage Area ── */}
        {step === 2 && (
          <>
            <div className="px-6 pt-5 pb-4">
              <button
                onClick={handleBack}
                className="flex items-center gap-1 text-indigo-500 text-xs font-semibold mb-4 hover:text-indigo-700 transition-colors"
              >
                <ArrowLeft className="w-3.5 h-3.5" /> BACK
              </button>
              <h2 className="text-xl font-bold text-gray-900">Select Storage Area</h2>
              <p className="text-gray-400 text-sm mt-1">Which specific area are you counting?</p>
            </div>

            <div className="px-3 pb-2 space-y-1 max-h-64 overflow-y-auto">
              {areasLoading ? (
                <div className="flex justify-center py-8">
                  <Loader className="w-5 h-5 text-indigo-400 animate-spin" />
                </div>
              ) : (
                <>
                  {storageAreas.map((area) => {
                    const active = selectedArea?.id === area.id
                    return (
                      <button
                        key={area.id}
                        onClick={() => setSelectedArea(area)}
                        className={`w-full flex items-center gap-3 px-4 py-3.5 rounded-xl border transition-all text-left ${
                          active
                            ? 'border-indigo-300 bg-indigo-50'
                            : 'border-gray-100 hover:bg-gray-50 hover:border-gray-200'
                        }`}
                      >
                        <div className={`w-9 h-9 rounded-full flex items-center justify-center shrink-0 ${active ? 'bg-indigo-600' : 'bg-gray-100'}`}>
                          <Package className={`w-4 h-4 ${active ? 'text-white' : 'text-gray-400'}`} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className={`font-semibold text-sm ${active ? 'text-indigo-700' : 'text-gray-800'}`}>{area.name}</p>
                          {area.description && <p className="text-gray-400 text-xs truncate">{area.description}</p>}
                        </div>
                        {active && <Check className="w-4 h-4 text-indigo-500 shrink-0" />}
                      </button>
                    )
                  })}

                  {showAddArea ? (
                    <div className="flex gap-2 px-1 pt-1">
                      <input
                        autoFocus
                        value={newAreaName}
                        onChange={(e) => setNewAreaName(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleAddArea()}
                        placeholder="e.g. Walk-in Fridge, Pantry"
                        className="flex-1 px-3 py-2 rounded-lg border border-gray-200 text-gray-800 text-sm outline-none focus:border-indigo-400"
                      />
                      <button
                        onClick={handleAddArea}
                        disabled={!newAreaName.trim() || addingArea}
                        className="px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm disabled:opacity-50"
                      >
                        {addingArea ? <Loader className="w-3.5 h-3.5 animate-spin" /> : 'Add'}
                      </button>
                      <button
                        onClick={() => { setShowAddArea(false); setNewAreaName('') }}
                        className="px-3 py-2 rounded-lg border border-gray-200 text-gray-400 text-sm"
                      >
                        ✕
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setShowAddArea(true)}
                      className="w-full flex items-center gap-3 px-4 py-3.5 rounded-xl border border-dashed border-gray-200 text-gray-400 hover:border-indigo-200 hover:text-indigo-400 transition-all text-sm"
                    >
                      <div className="w-9 h-9 rounded-full bg-gray-50 flex items-center justify-center shrink-0">
                        <Plus className="w-4 h-4" />
                      </div>
                      Add new area
                    </button>
                  )}

                  {storageAreas.length === 0 && !areasLoading && !showAddArea && (
                    <p className="text-center text-gray-400 text-sm py-4">No storage areas yet — add one above</p>
                  )}
                </>
              )}
            </div>

            <div className="px-4 pb-5 pt-3 space-y-2">
              <button
                onClick={handleConfirm}
                disabled={!selectedArea}
                className="w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-100 disabled:text-indigo-300 text-white font-semibold text-sm flex items-center justify-center gap-2 transition-all"
              >
                Start Counting <ChevronRight className="w-4 h-4" />
              </button>
              {onCancel && (
                <button onClick={onCancel} className="w-full text-center text-gray-400 text-sm py-1 hover:text-gray-600 transition-colors">
                  Cancel
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
