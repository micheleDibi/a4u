import { useTranslation } from "react-i18next";

interface Props {
  background?: string | null;
  logoLeft?: string | null;
  logoRight?: string | null;
  textColor: string;
  primaryColor: string;
  secondaryColor: string;
  fontFamily: string;
  slideSize: "16:9" | "4:3";
  /** Avatar mostrato in basso a destra. Se null, non viene renderizzato. */
  avatarUrl?: string | null;
}

export function SlideTemplatePreview({
  background,
  logoLeft,
  logoRight,
  textColor,
  primaryColor,
  secondaryColor,
  fontFamily,
  slideSize,
  avatarUrl,
}: Props) {
  const { t } = useTranslation();
  const aspect = slideSize === "16:9" ? "16/9" : "4/3";

  // Spazio a destra del corpo per evitare collisione con l'avatar (riservato 24%
  // del lato slide; l'avatar è 22%).
  const reservedRight = avatarUrl ? "26%" : "8%";

  return (
    <div
      className="relative w-full overflow-hidden rounded-lg shadow-xl"
      style={{
        aspectRatio: aspect,
        fontFamily: `${fontFamily}, "Inter", "Helvetica", "Arial", sans-serif`,
        color: textColor,
        containerType: "inline-size",
      }}
    >
      {/* Background */}
      {background ? (
        <img
          src={background}
          alt=""
          className="absolute inset-0 size-full object-cover"
        />
      ) : (
        <div
          className="absolute inset-0"
          style={{
            background: `linear-gradient(135deg, #ffffff 0%, ${primaryColor}0c 55%, ${secondaryColor}18 100%)`,
            backgroundColor: "#fafafa",
          }}
        />
      )}

      {/* Header */}
      <div className="absolute inset-x-[6%] top-[5%] flex items-start justify-between gap-2">
        <div className="flex max-h-[10%] items-center gap-[2cqw]">
          <div className="max-h-[5cqw] max-w-[12cqw]">
            {logoLeft ? (
              <img
                src={logoLeft}
                alt=""
                className="max-h-full max-w-full object-contain"
              />
            ) : (
              <div
                className="text-[1.05cqw] font-semibold uppercase tracking-[0.22em]"
                style={{ color: textColor, opacity: 0.55 }}
              >
                Università · Corso
              </div>
            )}
          </div>
        </div>
        {logoRight && (
          <div className="max-h-[5cqw] max-w-[10cqw]">
            <img
              src={logoRight}
              alt=""
              className="max-h-full max-w-full object-contain"
            />
          </div>
        )}
      </div>

      {/* Body */}
      <div
        className="absolute left-[6%] top-[24%] bottom-[18%] flex flex-col justify-center"
        style={{ right: reservedRight }}
      >
        <div
          className="text-[0.95cqw] font-semibold uppercase tracking-[0.24em]"
          style={{ color: textColor, opacity: 0.55 }}
        >
          {t("templates.preview.lessonTag")}
        </div>
        <div
          className="mt-[0.5cqw] text-[1cqw] font-semibold uppercase tracking-[0.22em]"
          style={{ color: primaryColor }}
        >
          {t("templates.preview.chapter")}
        </div>
        <div
          className="mt-[0.6cqw] text-[3.4cqw] font-semibold leading-[1.05] tracking-tight"
          style={{ color: textColor }}
        >
          {t("templates.preview.title")}
        </div>
        <div
          className="mt-[1cqw] max-w-[80%] text-[1.5cqw] font-light leading-snug"
          style={{ color: textColor, opacity: 0.7 }}
        >
          {t("templates.preview.subtitle")}
        </div>

        <ul className="mt-[2.2cqw] space-y-[0.8cqw]">
          {[
            t("templates.preview.bullet1"),
            t("templates.preview.bullet2"),
            t("templates.preview.bullet3"),
          ].map((line, i) => (
            <li
              key={i}
              className="flex items-start gap-[1.2cqw] text-[1.2cqw] leading-snug"
              style={{ color: textColor, opacity: 0.88 }}
            >
              <span
                className="mt-[0.55cqw] inline-block h-[1cqw] w-[1cqw] shrink-0 rounded-full"
                style={{
                  background: `linear-gradient(135deg, ${primaryColor}, ${secondaryColor})`,
                }}
              />
              <span>{line}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* Footer */}
      <div
        className="absolute inset-x-[6%] bottom-[5%] flex items-end justify-between gap-[2cqw]"
        style={{ paddingRight: avatarUrl ? "23%" : "0%" }}
      >
        <div className="min-w-0">
          <div
            className="h-[1px] w-[14cqw] opacity-30"
            style={{ backgroundColor: textColor }}
          />
          <div
            className="mt-[0.8cqw] flex flex-wrap items-center gap-x-[1.4cqw] gap-y-[0.2cqw] text-[1cqw]"
            style={{ color: textColor }}
          >
            <span className="font-semibold">
              {t("templates.preview.author")}
            </span>
            <span className="opacity-60">·</span>
            <span className="opacity-70">{t("templates.preview.role")}</span>
            <span className="opacity-60">·</span>
            <span className="opacity-70">{t("templates.preview.date")}</span>
          </div>
        </div>
        <div
          className="shrink-0 font-mono text-[1cqw] tabular-nums"
          style={{ color: textColor, opacity: 0.55 }}
        >
          01 / 24
        </div>
      </div>

      {/* Avatar in basso a destra — quadrato 1:1 */}
      {avatarUrl && (
        <div
          className="absolute bottom-[5%] right-[5%] aspect-square w-[22%] overflow-hidden rounded-lg ring-1 ring-black/10 shadow-[0_14px_38px_rgba(0,0,0,0.22)]"
          style={{ backgroundColor: "#0a0a0a" }}
        >
          <img
            src={avatarUrl}
            alt=""
            className="size-full object-cover"
          />
          {/* Etichetta sottile sotto l'avatar */}
          <div
            className="absolute inset-x-0 bottom-0 px-[0.6cqw] pb-[0.5cqw] pt-[1.2cqw] text-center text-[0.85cqw] font-semibold uppercase tracking-[0.18em] text-white/95"
            style={{
              background:
                "linear-gradient(to top, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0) 100%)",
            }}
          >
            {t("templates.preview.live")}
          </div>
        </div>
      )}
    </div>
  );
}
