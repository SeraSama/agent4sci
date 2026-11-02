// Cross-page UI state (current project) and shared server queries.
import { useQuery } from '@tanstack/react-query'
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { get } from './api'
import type { ModelsResponse, Project } from './types'

interface AppState {
  projectId: string | null
  sidebarCollapsed: boolean
  setProject: (id: string | null) => void
  toggleSidebar: () => void
}

export const useApp = create<AppState>()(
  persist(
    (set) => ({
      projectId: null,
      sidebarCollapsed: false,
      setProject: (projectId) => set({ projectId }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
    }),
    { name: 'a4s.app' },
  ),
)

export function useProjects() {
  return useQuery({ queryKey: ['projects'], queryFn: () => get<Project[]>('/projects') })
}

export function useCurrentProject(): Project | null {
  const { data } = useProjects()
  const projectId = useApp((s) => s.projectId)
  return data?.find((p) => p.id === projectId) ?? null
}

export function useModels() {
  return useQuery({ queryKey: ['models'], queryFn: () => get<ModelsResponse>('/models') })
}

export function useInfo() {
  return useQuery({
    queryKey: ['info'],
    queryFn: () => get<{ version: string; demo: boolean; data_dir: string; projects_dir: string; platform: string; default_model: string | null; secret_store: string }>('/info'),
  })
}
