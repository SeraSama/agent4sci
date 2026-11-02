// Chinese is the source language: UI strings are written in Chinese and used directly as keys.
// English translations map those strings (see en.ts). Missing translations fall back to Chinese.
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './en'

const saved = (() => {
  try {
    return localStorage.getItem('a4s.lang')
  } catch {
    return null
  }
})()

void i18n.use(initReactI18next).init({
  resources: { 'zh-CN': { translation: {} }, en: { translation: en } },
  lng: saved || 'zh-CN',
  fallbackLng: false,
  keySeparator: false,
  nsSeparator: false,
  returnEmptyString: false,
  interpolation: { escapeValue: false },
})

export function setLanguage(lang: string): void {
  void i18n.changeLanguage(lang)
  try {
    localStorage.setItem('a4s.lang', lang)
  } catch {
    // ignore
  }
  document.documentElement.lang = lang
}

export default i18n
