import { invoke } from '@tauri-apps/api/core'

export interface AppInfo {
  version: string
  name: string
}

export async function getAppInfo(): Promise<AppInfo> {
  return invoke<AppInfo>('get_app_info')
}

export async function ping(message: string): Promise<string> {
  return invoke<string>('ping', { message })
}
