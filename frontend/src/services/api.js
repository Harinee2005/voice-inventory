import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 35000 })

export const processVoiceText = (text, sessionId, workerId = 'worker', storageArea = null, locationName = null) =>
  api.post('/voice/process', {
    text,
    session_id: sessionId,
    worker_id: workerId,
    storage_area: storageArea,
    location_name: locationName,
  })

export const transcribeAudio = (formData) =>
  api.post('/voice/transcribe', formData, { headers: { 'Content-Type': 'multipart/form-data' } })

export const synthesizeSpeech = (text) =>
  api.post('/voice/synthesize', { text }, { responseType: 'blob' })

export const getInventory = (params = {}) => api.get('/inventory/', { params })
export const getAvailableDates = () => api.get('/inventory/dates/list')
export const createInventoryItem = (data) => api.post('/inventory/', data)
export const updateInventoryItem = (id, data) => api.patch(`/inventory/${id}`, data)
export const deleteInventoryItem = (id) => api.delete(`/inventory/${id}`)

export const getAnalytics = () => api.get('/analytics/')
export const getExpiringItems = (days = 3) => api.get('/analytics/expiring', { params: { days } })
export const getLowStock = () => api.get('/analytics/low-stock')

export const getConversation = (sessionId, limit = 50) =>
  api.get(`/conversations/${sessionId}`, { params: { limit } })
export const clearConversation = (sessionId) =>
  api.delete(`/conversations/${sessionId}`)
export const getActivityFeed = (limit = 30) =>
  api.get('/conversations/activity/feed', { params: { limit } })

export const getLocations = () => api.get('/locations/')
export const createLocation = (data) => api.post('/locations/', data)
export const deleteLocation = (id) => api.delete(`/locations/${id}`)
export const getStorageAreas = (locationId) => api.get(`/locations/${locationId}/storage-areas`)
export const createStorageArea = (locationId, data) => api.post(`/locations/${locationId}/storage-areas`, data)
export const deleteStorageArea = (id) => api.delete(`/locations/storage-areas/${id}`)

export const getUserProfile = (workerId) => api.get(`/users/${workerId}/profile`)
export const getUserLexicons = (workerId) => api.get(`/users/${workerId}/lexicons`)
export const deleteLexicon = (workerId, lexiconId) => api.delete(`/users/${workerId}/lexicons/${lexiconId}`)
export const getAllUserProfiles = () => api.get('/users/')
