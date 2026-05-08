interface Props {
  background?: string | null;
  logoLeft?: string | null;
  logoRight?: string | null;
  textColor: string;
  primaryColor: string;
  secondaryColor: string;
  fontFamily: string;
  pageSize: "A4" | "Letter";
  headerHeightMm: number;
  footerHeightMm: number;
  marginMm: number;
  /** Opacità dell'immagine di sfondo, 0-100. Default 15. */
  backgroundOpacityPct?: number;
}

const PAGE_HEIGHT_MM = { A4: 297, Letter: 279.4 };
const PAGE_RATIO = { A4: 210 / 297, Letter: 215.9 / 279.4 };

export function PdfTemplatePreview({
  background,
  logoLeft,
  logoRight,
  textColor,
  primaryColor,
  secondaryColor,
  fontFamily,
  pageSize,
  headerHeightMm,
  footerHeightMm,
  marginMm,
  backgroundOpacityPct = 15,
}: Props) {
  const bgOpacity = Math.max(0, Math.min(100, backgroundOpacityPct)) / 100;
  const totalMm = PAGE_HEIGHT_MM[pageSize];
  const headerPct = (headerHeightMm / totalMm) * 100;
  const footerPct = (footerHeightMm / totalMm) * 100;
  const marginVPct = (marginMm / totalMm) * 100;
  const marginHPct = (marginMm / (totalMm * PAGE_RATIO[pageSize])) * 100;

  return (
    <div
      className="relative w-full overflow-hidden rounded-lg bg-white shadow-xl"
      style={{
        aspectRatio: `${PAGE_RATIO[pageSize]}`,
        fontFamily: `${fontFamily}, "Inter", "Helvetica", "Arial", sans-serif`,
        color: textColor,
      }}
    >
      {background && (
        <img
          src={background}
          alt=""
          className="absolute inset-0 size-full object-cover"
          style={{ opacity: bgOpacity }}
        />
      )}

      <div
        className="absolute inset-x-0 top-0 flex items-center justify-between"
        style={{
          height: `${headerPct}%`,
          borderBottom: `2px solid ${primaryColor}`,
          paddingInline: `${marginHPct}%`,
        }}
      >
        <div className="h-[70%] max-w-[30%]">
          {logoLeft && (
            <img src={logoLeft} alt="" className="h-full max-w-full object-contain" />
          )}
        </div>
        <div className="h-[70%] max-w-[30%]">
          {logoRight && (
            <img src={logoRight} alt="" className="h-full max-w-full object-contain" />
          )}
        </div>
      </div>

      <div
        className="absolute flex flex-col"
        style={{
          top: `${headerPct + marginVPct}%`,
          bottom: `${footerPct + marginVPct}%`,
          insetInlineStart: `${marginHPct}%`,
          insetInlineEnd: `${marginHPct}%`,
          gap: "2%",
        }}
      >
        <div
          className="text-xs font-bold sm:text-base md:text-xl"
          style={{ color: textColor }}
        >
          Lorem ipsum dolor sit amet
        </div>
        <div className="h-[3px] w-12 rounded-full" style={{ backgroundColor: secondaryColor }} />
        {[100, 100, 100, 100, 60].map((w, i) => (
          <div
            key={i}
            className="h-1 sm:h-1.5 md:h-2 rounded"
            style={{ backgroundColor: textColor, opacity: 0.15, width: `${w}%` }}
          />
        ))}
      </div>

      <div
        className="absolute inset-x-0 bottom-0 flex items-center justify-between text-[8px] opacity-70 sm:text-[10px]"
        style={{
          height: `${footerPct}%`,
          borderTop: `1px solid ${primaryColor}55`,
          paddingInline: `${marginHPct}%`,
          color: textColor,
        }}
      >
        <span>a4u</span>
        <span>1</span>
      </div>
    </div>
  );
}
