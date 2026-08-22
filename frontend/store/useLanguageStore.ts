import { create } from "zustand";

export type AppLocale = "vi" | "ja";

const LANGUAGE_STORAGE_KEY = "ai-workforce-language-v2";

interface LanguageState {
  locale: AppLocale;
  hydrateLocale: () => void;
  setLocale: (locale: AppLocale) => void;
}

function applyDocumentLanguage(locale: AppLocale) {
  if (typeof document !== "undefined") {
    document.documentElement.lang = locale;
  }
}

export const useLanguageStore = create<LanguageState>((set) => ({
  locale: "ja",

  hydrateLocale: () => {
    if (typeof window === "undefined") return;
    const savedLocale = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    const locale: AppLocale = savedLocale === "vi" ? "vi" : "ja";
    applyDocumentLanguage(locale);
    set({ locale });
  },

  setLocale: (locale) => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, locale);
    }
    applyDocumentLanguage(locale);
    set({ locale });
  },
}));
