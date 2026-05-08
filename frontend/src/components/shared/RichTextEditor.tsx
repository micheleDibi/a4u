import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useEditor, EditorContent, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import { Markdown, type MarkdownStorage } from "tiptap-markdown";
import {
  Bold as BoldIcon,
  Italic as ItalicIcon,
  Strikethrough,
  Heading2,
  Heading3,
  List as ListIcon,
  ListOrdered,
  Quote,
  Link as LinkIcon,
  Code as CodeIcon,
  Undo2,
  Redo2,
} from "lucide-react";

import { cn } from "@/lib/utils";

interface RichTextEditorProps {
  value: string;
  onChange: (markdown: string) => void;
  placeholder?: string;
  disabled?: boolean;
  size?: "sm" | "md" | "lg";
  className?: string;
}

const SIZE_MIN_HEIGHT: Record<NonNullable<RichTextEditorProps["size"]>, string> = {
  sm: "min-h-[6rem]",
  md: "min-h-[10rem]",
  lg: "min-h-[16rem]",
};

export function RichTextEditor({
  value,
  onChange,
  placeholder,
  disabled = false,
  size = "md",
  className,
}: RichTextEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        // Markdown extension can already handle hard breaks; keep defaults.
        codeBlock: { HTMLAttributes: { class: "bg-muted rounded p-2 font-mono text-xs" } },
      }),
      Link.configure({
        openOnClick: false,
        autolink: true,
        HTMLAttributes: {
          class: "text-primary underline underline-offset-2",
          rel: "noopener noreferrer nofollow",
          target: "_blank",
        },
      }),
      Markdown.configure({
        html: false,
        tightLists: true,
        linkify: true,
        breaks: false,
        transformPastedText: true,
        transformCopiedText: true,
      }),
    ],
    content: protectTokens(value || ""),
    editable: !disabled,
    onUpdate: ({ editor }) => {
      const md = unprotectTokens(getMarkdownFromEditor(editor));
      onChange(md);
    },
    editorProps: {
      attributes: {
        class: cn(
          "prose prose-sm dark:prose-invert max-w-none focus:outline-none",
          "prose-p:my-2 prose-headings:my-3 prose-li:my-0",
          "px-4 py-3",
          SIZE_MIN_HEIGHT[size],
        ),
      },
    },
  });

  // Mantieni il contenuto sincronizzato se `value` cambia esternamente
  // (es. dialog riaperto con dati nuovi). Evita loop confrontando il
  // markdown corrente.
  useEffect(() => {
    if (!editor) return;
    const current = unprotectTokens(getMarkdownFromEditor(editor));
    if ((value || "") !== current) {
      editor.commands.setContent(protectTokens(value || ""), {
        emitUpdate: false,
      });
    }
  }, [editor, value]);

  useEffect(() => {
    if (!editor) return;
    editor.setEditable(!disabled);
  }, [editor, disabled]);

  if (!editor) {
    return (
      <div
        className={cn(
          "rounded-md border bg-muted/20",
          SIZE_MIN_HEIGHT[size],
          className,
        )}
      />
    );
  }

  return (
    <div
      className={cn(
        "rounded-md border bg-background",
        disabled && "opacity-60",
        className,
      )}
    >
      <Toolbar editor={editor} disabled={disabled} />
      <EditorContent
        editor={editor}
        className="text-sm"
        data-placeholder={placeholder}
      />
    </div>
  );
}

interface ToolbarProps {
  editor: Editor;
  disabled: boolean;
}

function Toolbar({ editor, disabled }: ToolbarProps) {
  const { t } = useTranslation();

  return (
    <div className="sticky top-0 z-10 flex flex-wrap items-center gap-0.5 border-b bg-muted/40 px-1.5 py-1">
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.bold")}
        onClick={() => editor.chain().focus().toggleBold().run()}
        active={editor.isActive("bold")}
        disabled={disabled}
      >
        <BoldIcon className="size-3.5" />
      </ToolbarBtn>
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.italic")}
        onClick={() => editor.chain().focus().toggleItalic().run()}
        active={editor.isActive("italic")}
        disabled={disabled}
      >
        <ItalicIcon className="size-3.5" />
      </ToolbarBtn>
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.strike")}
        onClick={() => editor.chain().focus().toggleStrike().run()}
        active={editor.isActive("strike")}
        disabled={disabled}
      >
        <Strikethrough className="size-3.5" />
      </ToolbarBtn>
      <Divider />
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.h2")}
        onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
        active={editor.isActive("heading", { level: 2 })}
        disabled={disabled}
      >
        <Heading2 className="size-3.5" />
      </ToolbarBtn>
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.h3")}
        onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
        active={editor.isActive("heading", { level: 3 })}
        disabled={disabled}
      >
        <Heading3 className="size-3.5" />
      </ToolbarBtn>
      <Divider />
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.bulletList")}
        onClick={() => editor.chain().focus().toggleBulletList().run()}
        active={editor.isActive("bulletList")}
        disabled={disabled}
      >
        <ListIcon className="size-3.5" />
      </ToolbarBtn>
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.orderedList")}
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
        active={editor.isActive("orderedList")}
        disabled={disabled}
      >
        <ListOrdered className="size-3.5" />
      </ToolbarBtn>
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.quote")}
        onClick={() => editor.chain().focus().toggleBlockquote().run()}
        active={editor.isActive("blockquote")}
        disabled={disabled}
      >
        <Quote className="size-3.5" />
      </ToolbarBtn>
      <Divider />
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.inlineCode")}
        onClick={() => editor.chain().focus().toggleCode().run()}
        active={editor.isActive("code")}
        disabled={disabled}
      >
        <CodeIcon className="size-3.5" />
      </ToolbarBtn>
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.link")}
        onClick={() => promptLink(editor)}
        active={editor.isActive("link")}
        disabled={disabled}
      >
        <LinkIcon className="size-3.5" />
      </ToolbarBtn>
      <Divider />
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.undo")}
        onClick={() => editor.chain().focus().undo().run()}
        disabled={disabled || !editor.can().undo()}
      >
        <Undo2 className="size-3.5" />
      </ToolbarBtn>
      <ToolbarBtn
        title={t("courses.lessonsContent.editorUI.richtext.redo")}
        onClick={() => editor.chain().focus().redo().run()}
        disabled={disabled || !editor.can().redo()}
      >
        <Redo2 className="size-3.5" />
      </ToolbarBtn>
    </div>
  );
}

interface ToolbarBtnProps {
  title: string;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  children: React.ReactNode;
}

function ToolbarBtn({ title, onClick, active, disabled, children }: ToolbarBtnProps) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "inline-flex h-7 w-7 items-center justify-center rounded text-muted-foreground transition-colors",
        "hover:bg-accent hover:text-accent-foreground",
        "disabled:pointer-events-none disabled:opacity-50",
        active && "bg-accent text-accent-foreground",
      )}
    >
      {children}
    </button>
  );
}

function Divider() {
  return <span className="mx-0.5 h-5 w-px bg-border" aria-hidden />;
}

function getMarkdownFromEditor(editor: Editor): string {
  const storage = (editor.storage as unknown as Record<string, unknown>)
    .markdown as MarkdownStorage | undefined;
  return storage?.getMarkdown() ?? "";
}

// === Token protection (asset refs + math) =================================
//
// Il serializer di prosemirror-markdown fa l'escape di `[`, `]`, `\`, `*`,
// `_`, `~`, `` ` `` quando appaiono come testo. Questo distrugge i
// riferimenti ad asset `[FIG:..]/[TAB:..]/[EQ:..]/[EX:..]` (diventano
// `\[..\]`) e le formule LaTeX inline `$P \lor \neg P$` (diventano
// `$P \\lor \\neg P$`).
//
// Per preservarli senza scrivere un'estensione TipTap dedicata avvolgiamo
// questi token in inline code (backtick) prima di passarli all'editor;
// ProseMirror serializza inline code in modo verbatim, senza escape. In
// uscita li rimuoviamo per ripristinare il markdown originale.
//
// Visivamente, in editor i token appaiono come "chip" monospace — leggibili
// e distinti dal testo. Il rendering vero (KaTeX, blocchi asset) avviene
// nella view (`MarkdownRenderer`), invariata.

const ASSET_REF_RE = /\[(?:FIG|TAB|EQ|EX):[^\]\n]+\]/g;
const DISPLAY_MATH_RE = /\$\$[\s\S]+?\$\$/g;
const INLINE_MATH_RE = /(?<!\$)\$(?!\$)[^$\n]+?\$(?!\$)/g;

function protectTokens(md: string): string {
  if (!md) return "";
  // 1) "Estrai" gli inline-code esistenti per non riavvolgerli.
  const codeSpans: string[] = [];
  let work = md.replace(/`[^`\n]*`/g, (m) => {
    codeSpans.push(m);
    return `###CS${codeSpans.length - 1}###`;
  });
  // 2) Avvolgi i token in backtick.
  work = work.replace(ASSET_REF_RE, (m) => "`" + m + "`");
  work = work.replace(DISPLAY_MATH_RE, (m) => "`" + m + "`");
  work = work.replace(INLINE_MATH_RE, (m) => "`" + m + "`");
  // 3) Ripristina gli inline-code preesistenti.
  return work.replace(/###CS(\d+)###/g, (_m, idx) => codeSpans[Number(idx)]);
}

function unprotectTokens(md: string): string {
  if (!md) return "";
  let out = md;
  // Rimuovi i backtick avvolti dai nostri token protetti.
  out = out.replace(
    /`(\[(?:FIG|TAB|EQ|EX):[^\]\n]+\])`/g,
    (_m, inner) => inner,
  );
  out = out.replace(/`(\$\$[\s\S]+?\$\$)`/g, (_m, inner) => inner);
  out = out.replace(/`(\$[^$\n]+?\$)`/g, (_m, inner) => inner);
  // Recupera anche eventuali asset ref scappati manualmente (`\[EQ:..\]`).
  out = out.replace(
    /\\(\[(?:FIG|TAB|EQ|EX):[^\]\n]+)\\(\])/g,
    (_m, body, close) => body + close,
  );
  return out;
}

function promptLink(editor: Editor) {
  const previous = editor.getAttributes("link").href as string | undefined;
  // eslint-disable-next-line no-alert
  const url = window.prompt("URL", previous ?? "https://");
  if (url === null) return; // annullato
  if (url === "") {
    editor.chain().focus().extendMarkRange("link").unsetLink().run();
    return;
  }
  editor
    .chain()
    .focus()
    .extendMarkRange("link")
    .setLink({ href: url })
    .run();
}

export default RichTextEditor;
