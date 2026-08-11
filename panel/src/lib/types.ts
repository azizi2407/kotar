export interface User {
  sub: string
  email: string
  name: string | null
  role: string // pending | management | designer | videographer | content_creator | client
}

export interface Contract {
  weekly_content_count: number | null
  post_count: number | null
  story_count: number | null
  content_plan: string | null
  content_types: string[] | null
  special_sharing_types: string[] | null
  description: string | null
  vat_rate: number | null
  fee_effective_date: string | null
  video_shooting_enabled: boolean | null
  weekly_video_count: number | null
  photo_shooting_enabled: boolean | null
  weekly_photo_count: number | null
  drone_usage: boolean | null
  location_notes: string | null
}

export interface Contact {
  name: string | null
  email: string | null
  phone: string | null
  notes: string | null
}

export interface ClientLocation {
  name: string | null
  address: string | null
}

export interface WeekFolder {
  week_number: number
  folder_id: string | null
  name: string | null
  link: string | null
}

// Role slot -> SSO sub (user_id)
export type TeamAssignments = Record<string, string>

// Fields marked `?:` are ONLY returned to management — the backend's `_client_json()`
// never puts these keys in the response for production roles (commercial + contact
// info + internal notes + public token). That's why the type is optional; `undefined`
// means "you don't have access", `null` means "empty".
export interface ClientListItem {
  id: number
  name: string
  status: string
  sector: string | null
  client_email?: string | null
  instagram_url: string | null
  created_at: string | null
  created_by: string | null
  updated_at: string | null
  updated_by: string | null
  team_assignments: TeamAssignments
}

export interface ClientDetail extends ClientListItem {
  brief_enabled: boolean
  notes?: string | null
  google_drive_url: string | null
  special_days_token?: string | null
  sharing_playbook: unknown
  drive_meta: unknown
  deleted_at: string | null
  deleted_by: string | null
  deleted_reason: string | null
  restored_at: string | null
  restored_by: string | null
  contract?: Contract | null
  contacts?: Contact[]
  locations?: ClientLocation[]
  week_folders: WeekFolder[]
}
