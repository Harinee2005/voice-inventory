import { useState, useCallback } from 'react'
import { Wifi, MapPin, Mic } from 'lucide-react'
import Sidebar from './components/Sidebar'
import VoiceAssistant from './components/VoiceAssistant'
import InventoryTable from './components/InventoryTable'
import Analytics from './components/Analytics'
import ActivityFeed from './components/ActivityFeed'
import ConversationHistory from './components/ConversationHistory'
import LocationSetup from './components/LocationSetup'
import StorageAreasPage from './components/StorageAreasPage'
import LocationsPage from './components/LocationsPage'
import VoiceAuditModal from './components/VoiceAuditModal'
import { useWebSocket } from './hooks/useWebSocket'

function getOrCreateSessionId() {
  let id = localStorage.getItem('aria_session_id')
  if (!id) {
    id = `session_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
    localStorage.setItem('aria_session_id', id)
  }
  return id
}

function PageHeader({ title, subtitle }) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-bold text-gray-900">{title}</h1>
      {subtitle && <p className="text-gray-400 text-sm mt-0.5">{subtitle}</p>}
    </div>
  )
}

export default function App() {
  const [sessionId] = useState(getOrCreateSessionId)
  const [workerName, setWorkerName] = useState('worker_1')
  const [activeTab, setActiveTab] = useState('dashboard')
  const [refreshKey, setRefreshKey] = useState(0)
  const [workspace, setWorkspace] = useState(null)
  const [showWorkspaceModal, setShowWorkspaceModal] = useState(false)
  const [showVoiceModal, setShowVoiceModal] = useState(false)
  const [freshSession, setFreshSession] = useState(false)
  const [hasGreeted, setHasGreeted] = useState(false)

  const handleInventoryUpdate = useCallback(() => setRefreshKey((k) => k + 1), [])

  useWebSocket(useCallback((msg) => {
    if (msg.type === 'inventory_update') setRefreshKey((k) => k + 1)
  }, []))

  const handleSetupComplete = useCallback((setup) => {
    setWorkspace(setup)
    setShowWorkspaceModal(false)
    setHasGreeted(false) // reset so greeting fires once for the new workspace
  }, [])

  const handleChangeWorkspace = useCallback(() => {
    setShowWorkspaceModal(true)
  }, [])

  const handleMicClick = useCallback(() => {
    if (!workspace) {
      setShowWorkspaceModal(true)
    } else {
      setFreshSession(false)
      setShowVoiceModal(true)
    }
  }, [workspace])

  return (
    <div className="flex min-h-screen bg-gray-50 relative">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        workerName={workerName}
        onWorkerChange={setWorkerName}
        onChangeWorkspace={handleChangeWorkspace}
      />

      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header className="h-14 bg-white border-b border-gray-200 flex items-center justify-between px-6 shrink-0">
          {workspace ? (
            <div className="flex items-center gap-2 text-sm">
              <MapPin className="w-4 h-4 text-indigo-400 shrink-0" />
              <span className="text-gray-400">{workspace.locationName}</span>
              <span className="text-gray-200 mx-0.5">›</span>
              <span className="font-semibold text-gray-700">{workspace.storageAreaName}</span>
            </div>
          ) : (
            <button
              onClick={() => setShowWorkspaceModal(true)}
              className="flex items-center gap-2 text-sm text-indigo-500 hover:text-indigo-700 font-medium transition-colors"
            >
              <MapPin className="w-4 h-4" /> Select workspace
            </button>
          )}
          <div className="flex items-center gap-1.5 text-xs text-emerald-600 font-medium">
            <Wifi className="w-3.5 h-3.5" />
            Connected
          </div>
        </header>

        <main className="flex-1 p-6 overflow-auto">

          {activeTab === 'dashboard' && (
            <div>
              <PageHeader title="Dashboard" subtitle="Live view of your current workspace" />
              <div className={`grid gap-5 h-[calc(100vh-196px)] ${workspace ? 'grid-cols-1 xl:grid-cols-[1fr_380px]' : 'grid-cols-1'}`}>
                <div className="flex-1 min-h-0">
                  <InventoryTable refreshTrigger={refreshKey} workspace={workspace} />
                </div>
                {workspace && (
                  <div className="h-full min-h-[560px]">
                    <VoiceAssistant
                      sessionId={sessionId}
                      workerId={workerName}
                      onInventoryUpdate={handleInventoryUpdate}
                      storageArea={workspace.storageAreaName}
                      locationName={workspace.locationName}
                      shouldGreet={!hasGreeted}
                      onGreeted={() => setHasGreeted(true)}
                    />
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === 'locations' && (
            <div>
              <PageHeader title="Location Management" subtitle="Manage individual restaurant sites and their inventory contexts." />
              <LocationsPage refreshTrigger={refreshKey} />
            </div>
          )}

          {activeTab === 'inventory' && (
            <div className="h-[calc(100vh-140px)] flex flex-col">
              <PageHeader title="Live Inventory" subtitle="Real-time stock tracking." />
              <div className="flex-1 min-h-0">
                <InventoryTable refreshTrigger={refreshKey} />
              </div>
            </div>
          )}

          {activeTab === 'analytics' && (
            <div>
              <PageHeader title="Inventory Analytics" subtitle="Real-time insights into your inventory" />
              <Analytics refreshTrigger={refreshKey} />
            </div>
          )}

          {activeTab === 'storage' && (
            <div>
              <PageHeader title="Storage Areas" subtitle="Capacity monitoring across your locations." />
              <StorageAreasPage refreshTrigger={refreshKey} />
            </div>
          )}

          {activeTab === 'history' && (
            <div>
              <PageHeader title="Count History" subtitle="Audit logs and session reports." />
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                <div className="bg-white rounded-2xl border border-gray-200 p-5">
                  <h3 className="text-gray-800 font-semibold text-sm mb-4">Conversation Log</h3>
                  <ConversationHistory sessionId={sessionId} refreshTrigger={refreshKey} />
                </div>
                <ActivityFeed refreshTrigger={refreshKey} />
              </div>
            </div>
          )}

        </main>
      </div>

      {/* Floating mic — hidden on dashboard when workspace is set (voice panel handles it there) */}
      {!(activeTab === 'dashboard' && workspace) && (
        <button
          onClick={handleMicClick}
          className="fixed bottom-6 right-6 w-14 h-14 rounded-full bg-indigo-600 hover:bg-indigo-700 shadow-lg shadow-indigo-200 flex items-center justify-center transition-all duration-200 z-40 hover:scale-105"
          title={workspace ? 'Voice Audit' : 'Click to begin — select workspace first'}
        >
          <Mic className="w-6 h-6 text-white" />
        </button>
      )}

      {/* Workspace setup modal */}
      {showWorkspaceModal && (
        <LocationSetup
          onComplete={handleSetupComplete}
          onCancel={workspace ? () => setShowWorkspaceModal(false) : null}
        />
      )}

      {/* Voice audit modal */}
      <VoiceAuditModal
        isOpen={showVoiceModal}
        onClose={() => { setShowVoiceModal(false); setFreshSession(false) }}
        workspace={workspace}
        sessionId={sessionId}
        workerId={workerName}
        onInventoryUpdate={handleInventoryUpdate}
        greetOnOpen={freshSession}
      />
    </div>
  )
}
