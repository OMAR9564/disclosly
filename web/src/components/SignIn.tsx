"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { Disclaimer } from "@/components/Disclaimer";
import { FlapRow } from "@/components/FlapRow";
import { LanguageToggle } from "@/components/LanguageToggle";
import { ProviderGlyph } from "@/components/ProviderGlyph";
import { Turnstile } from "@/components/Turnstile";
import type { Dictionary } from "@/i18n";
import { useLanguage } from "@/i18n/useLanguage";
import {
  ApiError,
  type AuthProvider,
  navigation,
  startSignIn,
} from "@/lib/api";
import { errorMessage } from "@/lib/messages";

const PROVIDERS: AuthProvider[] = ["google", "github"];
const CALLBACK_REASONS = new Set([
  "invalid_state",
  "invalid_code",
  "email_unverified",
  "provider_error",
]);

/** Callback failure reasons (contracts/http-api.md) to visitor text. */
export function signInError(
  reason: string | null,
  t: Dictionary,
): string | null {
  switch (reason) {
    case null:
    case "":
      return null;
    case "invalid_state":
    case "invalid_code":
      return t.signinErrorState;
    case "email_unverified":
      return t.signinErrorEmail;
    case "bot_check_failed":
      return t.signinErrorBot;
    default:
      return t.signinErrorProvider;
  }
}

/** Only same-site paths go back to the API as `return_to`. */
export function safeReturnTo(value: string | null): string {
  return value &&
    value.startsWith("/") &&
    !value.startsWith("//") &&
    !value.includes("\\")
    ? value
    : "/";
}

export function SignIn({
  siteKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "",
}: {
  siteKey?: string;
}) {
  const { language, t, toggle } = useLanguage();
  const params = useSearchParams();
  const [token, setToken] = useState<string | null>(null);
  const [botFailed, setBotFailed] = useState(false);
  const [pending, setPending] = useState<AuthProvider | null>(null);
  // A callback reason or an API error code; turned into text on render so it follows the language.
  const [failure, setFailure] = useState<string | null>(params.get("error"));
  const failureText =
    failure === null
      ? null
      : CALLBACK_REASONS.has(failure) || failure === "bot_check_failed"
        ? signInError(failure, t)
        : errorMessage(failure, t);

  const go = async (provider: AuthProvider) => {
    if (!token) return;
    setPending(provider);
    setFailure(null);
    try {
      const { authorize_url } = await startSignIn(
        provider,
        token,
        safeReturnTo(params.get("return_to")),
        language,
      );
      navigation.assign(authorize_url);
    } catch (error) {
      const code = error instanceof ApiError ? error.code : "internal_error";
      setFailure(code);
      setToken(null);
      setPending(null);
    }
  };

  // No site key means the build lacked NEXT_PUBLIC_TURNSTILE_SITE_KEY: say so instead of waiting.
  const status =
    botFailed || !siteKey
      ? t.signinBotFailed
      : token
        ? t.signinBotReady
        : t.signinBotWaiting;

  return (
    <main className="board">
      <header className="board__top">
        <Link href="/" className="board__sources board__home" lang="en">
          Disclosly
        </Link>
        <LanguageToggle t={t} onToggle={toggle} />
      </header>

      <section className="gate" aria-labelledby="signin-title">
        <div className="row">
          <p className="row__label" aria-hidden="true">
            {t.signinGate}
          </p>
          <h1 id="signin-title" className="gate__name">
            <span className="sr-only">{t.signinTitle}</span>
            <FlapRow text={t.signinTitle} length={8} locale={t.locale} />
          </h1>
        </div>
        <p className="placard">{t.signinIntro}</p>

        <div className="plate plate--bot">
          <p className="plate__title">{t.signinBotLabel}</p>
          {siteKey ? (
            <Turnstile
              siteKey={siteKey}
              language={language}
              onToken={(value) => {
                setToken(value);
                if (value) setBotFailed(false);
              }}
              onError={() => {
                setToken(null);
                setBotFailed(true);
              }}
            />
          ) : null}
        </div>

        <div className="gate__keys">
          {PROVIDERS.map((provider) => (
            <button
              key={provider}
              type="button"
              className="key key--provider"
              disabled={!token || pending !== null}
              aria-describedby="signin-status"
              onClick={() => void go(provider)}
            >
              <ProviderGlyph provider={provider} />
              {pending === provider
                ? t.signinRedirecting
                : provider === "google"
                  ? t.signinGoogle
                  : t.signinGithub}
            </button>
          ))}
        </div>
        <p id="signin-status" className="ask__note" aria-live="polite">
          {status}
        </p>
        {failureText ? (
          <p className="gate__error" role="alert">
            {failureText}
          </p>
        ) : null}
      </section>

      <div className="board__foot">
        <nav className="legal-links" aria-label={t.disclaimerLabel}>
          <Link href="/privacy">{t.privacyLink}</Link>
          <span aria-hidden="true">·</span>
          <Link href="/terms">{t.termsLink}</Link>
        </nav>
        <Disclaimer t={t} />
      </div>
    </main>
  );
}
