import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import de from "./de.json";
import en from "./en.json";

/**
 * German first, English as the fallback.
 *
 * The service this administers is operated at a German university and its
 * operators read German; the package itself is English, and so is every
 * message the API produces. Both are true, which is why the interface carries
 * both rather than picking one.
 */
export const FALLBACK_LANGUAGE = "en";

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: { de: { translation: de }, en: { translation: en } },
    fallbackLng: FALLBACK_LANGUAGE,
    supportedLngs: ["de", "en"],
    interpolation: { escapeValue: false },
  });

/** The two-letter language currently in effect, for an `Accept-Language`. */
export function currentLanguage(): string {
  return (i18n.resolvedLanguage || FALLBACK_LANGUAGE).slice(0, 2);
}

export default i18n;
