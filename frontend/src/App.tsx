import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiClients } from "./components/ApiClients";
import { Catalogue } from "./components/Catalogue";
import { Credentials } from "./components/Credentials";
import { Templates } from "./components/Templates";
import { Tenants } from "./components/Tenants";

const TABS = ["templates", "credentials", "clients", "catalogue"] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const { t, i18n } = useTranslation();
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("templates");

  return (
    <main>
      <header>
        <h1>{t("app.title")}</h1>
        <p>{t("app.subtitle")}</p>
        <div className="languages">
          {["de", "en"].map((language) => (
            <button
              key={language}
              aria-pressed={i18n.resolvedLanguage === language}
              onClick={() => void i18n.changeLanguage(language)}
            >
              {language.toUpperCase()}
            </button>
          ))}
        </div>
      </header>

      <Tenants selected={tenantId} onSelect={setTenantId} />

      {tenantId ? (
        <>
          <nav>
            {TABS.map((name) => (
              <button
                key={name}
                aria-pressed={tab === name}
                onClick={() => setTab(name)}
              >
                {t(`tabs.${name}`)}
              </button>
            ))}
          </nav>
          {tab === "templates" ? <Templates tenantId={tenantId} /> : null}
          {tab === "credentials" ? <Credentials tenantId={tenantId} /> : null}
          {tab === "clients" ? <ApiClients tenantId={tenantId} /> : null}
          {tab === "catalogue" ? <Catalogue tenantId={tenantId} /> : null}
        </>
      ) : null}
    </main>
  );
}
