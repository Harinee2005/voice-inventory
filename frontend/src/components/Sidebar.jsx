import { LayoutDashboard, MapPin, Package, BarChart3, Grid3X3, History, Users, Settings, LogOut, Cpu, ChevronDown } from 'lucide-react'

const NAV = [
  { id: 'dashboard',  label: 'Dashboard',      icon: LayoutDashboard },
  { id: 'locations',  label: 'Locations',       icon: MapPin },
  { id: 'inventory',  label: 'Live Inventory',  icon: Package },
  { id: 'analytics',  label: 'Analytics',       icon: BarChart3 },
  { id: 'storage',    label: 'Storage Areas',   icon: Grid3X3 },
  { id: 'history',    label: 'Count History',   icon: History },
]

const ADMIN = [
  { id: 'team',     label: 'Team Management',    icon: Users,    disabled: true },
  { id: 'settings', label: 'Restaurant Settings', icon: Settings, disabled: true },
]

export default function Sidebar({ activeTab, setActiveTab, workerName, onWorkerChange, onChangeWorkspace }) {
  return (
    <aside className="w-[220px] shrink-0 bg-white border-r border-gray-200 flex flex-col min-h-screen sticky top-0">
      {/* Logo */}
      <div className="px-5 py-5 border-b border-gray-100">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl bg-indigo-600 flex items-center justify-center shrink-0">
            <Cpu className="w-5 h-5 text-white" />
          </div>
          <div>
            <p className="font-bold text-gray-900 text-sm leading-tight">ARIA</p>
            <p className="text-gray-400 text-xs leading-tight">Inventory Assistant</p>
          </div>
        </div>
      </div>

      {/* Main nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {NAV.map(({ id, label, icon: Icon }) => {
          const active = activeTab === id
          return (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 ${
                active
                  ? 'bg-indigo-50 text-indigo-600'
                  : 'text-gray-500 hover:bg-gray-50 hover:text-gray-800'
              }`}
            >
              <Icon className={`w-4 h-4 shrink-0 ${active ? 'text-indigo-600' : 'text-gray-400'}`} />
              {label}
            </button>
          )
        })}

        {/* Admin section */}
        <div className="pt-4">
          <p className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
            Administration
          </p>
          {ADMIN.map(({ id, label, icon: Icon, disabled }) => (
            <button
              key={id}
              disabled={disabled}
              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-gray-400 cursor-not-allowed opacity-50"
            >
              <Icon className="w-4 h-4 shrink-0" />
              {label}
            </button>
          ))}
        </div>
      </nav>

      {/* User profile */}
      <div className="px-3 py-4 border-t border-gray-100 space-y-1">
        <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-gray-50">
          <div className="w-7 h-7 rounded-full bg-indigo-600 flex items-center justify-center text-white text-xs font-bold shrink-0">
            {workerName?.[0]?.toUpperCase() || 'W'}
          </div>
          <div className="flex-1 min-w-0">
            <input
              value={workerName}
              onChange={(e) => onWorkerChange(e.target.value)}
              className="bg-transparent text-gray-800 text-xs font-medium w-full outline-none truncate"
              placeholder="Worker name"
            />
            <p className="text-gray-400 text-[10px] uppercase tracking-wide">Staff</p>
          </div>
        </div>
        <button
          onClick={onChangeWorkspace}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs text-gray-500 hover:bg-gray-50 hover:text-gray-700 transition-colors"
        >
          <LogOut className="w-3.5 h-3.5" />
          Change Workspace
        </button>
      </div>
    </aside>
  )
}
