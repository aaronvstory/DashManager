import { useState } from "react"
import { IdCard } from "lucide-react"
import { Input } from "@/components/ui/input"
import {
  Field,
  SettingsCard,
  useSaveSettings,
  type IdentityCaptureSettings,
} from "@/components/settings/shared"

/**
 * Profile-scrape configuration — the 'identity_capture' settings object.
 * Inputs are located by their visible form labels, which survive DoorDash
 * deploys better than hashed CSS classes.
 */
export function IdentityCaptureSection({
  identity,
}: {
  identity: IdentityCaptureSettings
}) {
  const initial = {
    url: identity.url,
    firstName: identity.labels.first_name,
    lastName: identity.labels.last_name,
    email: identity.labels.email,
    phone: identity.labels.phone,
  }
  const [form, setForm] = useState(initial)
  const [baseline, setBaseline] = useState(initial)

  const set = <K extends keyof typeof initial>(field: K, value: string) =>
    setForm((f) => ({ ...f, [field]: value }))

  const dirty = JSON.stringify(form) !== JSON.stringify(baseline)
  const invalid = form.url.trim() === ""
  const save = useSaveSettings("Identity capture")

  function handleSave() {
    save.mutate(
      [
        {
          key: "identity_capture",
          value: {
            ...identity,
            url: form.url.trim(),
            labels: {
              ...identity.labels,
              first_name: form.firstName.trim(),
              last_name: form.lastName.trim(),
              email: form.email.trim(),
              phone: form.phone.trim(),
            },
          },
        },
      ],
      { onSuccess: () => setBaseline(form) },
    )
  }

  return (
    <SettingsCard
      icon={IdCard}
      title="Identity capture"
      description="Where customer profile details are read after login, and the visible form labels used to find each input."
      dirty={dirty}
      saving={save.isPending}
      invalid={invalid}
      onSave={handleSave}
    >
      <Field
        label="Profile URL"
        htmlFor="identity-url"
        helper="The DoorDash edit-profile page scraped right after a login is captured."
      >
        <Input
          id="identity-url"
          value={form.url}
          onChange={(e) => set("url", e.target.value)}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={form.url.trim() === ""}
        />
      </Field>

      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="First name label" htmlFor="identity-first-name">
          <Input
            id="identity-first-name"
            value={form.firstName}
            onChange={(e) => set("firstName", e.target.value)}
            autoComplete="off"
          />
        </Field>
        <Field label="Last name label" htmlFor="identity-last-name">
          <Input
            id="identity-last-name"
            value={form.lastName}
            onChange={(e) => set("lastName", e.target.value)}
            autoComplete="off"
          />
        </Field>
        <Field label="Email label" htmlFor="identity-email">
          <Input
            id="identity-email"
            value={form.email}
            onChange={(e) => set("email", e.target.value)}
            autoComplete="off"
          />
        </Field>
        <Field label="Phone label" htmlFor="identity-phone">
          <Input
            id="identity-phone"
            value={form.phone}
            onChange={(e) => set("phone", e.target.value)}
            autoComplete="off"
          />
        </Field>
      </div>
    </SettingsCard>
  )
}
