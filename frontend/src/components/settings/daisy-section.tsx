/**
 * CustomerDaisy section — where the CustomerDaisy checkout lives (root + venv
 * python) and the default account-creation location/radius. Cross-platform:
 * on macOS/Linux point `root` at your checkout; leave `python` blank to
 * auto-detect (.venv/bin/python or .venv/Scripts/python.exe).
 */
import { useState } from "react"
import { Flower2 } from "lucide-react"
import { Input } from "@/components/ui/input"
import {
  Field,
  SettingsCard,
  Token,
  parsePositiveInt,
  useSaveSettings,
  type DaisySettings,
} from "./shared"

export function DaisySection({ daisy }: { daisy: DaisySettings }) {
  const save = useSaveSettings("CustomerDaisy")
  const [root, setRoot] = useState(daisy.root ?? "")
  const [python, setPython] = useState(daisy.python ?? "")
  const [origin, setOrigin] = useState(daisy.location_origin ?? "")
  const [radius, setRadius] = useState(String(daisy.radius_miles ?? 5))
  const [pwd, setPwd] = useState(daisy.default_password ?? "")

  const radiusValid = parsePositiveInt(radius) !== null || radius.trim() === ""
  const dirty =
    root !== (daisy.root ?? "") ||
    python !== (daisy.python ?? "") ||
    origin !== (daisy.location_origin ?? "") ||
    radius !== String(daisy.radius_miles ?? 5) ||
    pwd !== (daisy.default_password ?? "")

  function onSave() {
    const r = Number(radius)
    save.mutate([
      {
        key: "daisy",
        value: {
          ...daisy,
          root: root.trim(),
          python: python.trim(),
          location_origin: origin.trim(),
          radius_miles: Number.isFinite(r) && r > 0 ? r : 5,
          default_password: pwd,
        },
      },
    ])
  }

  return (
    <SettingsCard
      icon={Flower2}
      title="CustomerDaisy"
      description="Where the CustomerDaisy checkout lives + default signup location. DashManager shells out to its venv to generate identities and rent numbers."
      dirty={dirty}
      saving={save.isPending}
      invalid={!radiusValid}
      onSave={onSave}
    >
      <Field
        label="CustomerDaisy root"
        htmlFor="daisy-root"
        helper={
          <>
            Absolute path to the CustomerDaisy checkout. Windows:{" "}
            <Token>C:\claude\CustomerDaisy</Token>; macOS/Linux:{" "}
            <Token>~/CustomerDaisy</Token>.
          </>
        }
      >
        <Input
          id="daisy-root"
          value={root}
          onChange={(e) => setRoot(e.target.value)}
          placeholder="/path/to/CustomerDaisy"
        />
      </Field>

      <Field
        label="Python interpreter (optional)"
        htmlFor="daisy-python"
        helper={
          <>
            Leave blank to auto-detect the checkout's venv (
            <Token>.venv/bin/python</Token> on macOS/Linux,{" "}
            <Token>.venv/Scripts/python.exe</Token> on Windows).
          </>
        }
      >
        <Input
          id="daisy-python"
          value={python}
          onChange={(e) => setPython(e.target.value)}
          placeholder="(auto-detect)"
        />
      </Field>

      <Field
        label="Default location"
        htmlFor="daisy-origin"
        helper="The address signups generate a nearby identity around (overridable per run)."
      >
        <Input
          id="daisy-origin"
          value={origin}
          onChange={(e) => setOrigin(e.target.value)}
        />
      </Field>

      <Field label="Default radius (miles)" htmlFor="daisy-radius">
        <Input
          id="daisy-radius"
          type="number"
          min={1}
          value={radius}
          onChange={(e) => setRadius(e.target.value)}
        />
      </Field>

      <Field
        label="Default password"
        htmlFor="daisy-pwd"
        helper="Shared Mail.tm + DoorDash password CustomerDaisy assigns; used for relogin when a row has no stored password. Stored locally in SQLite only."
      >
        <Input
          id="daisy-pwd"
          type="password"
          value={pwd}
          onChange={(e) => setPwd(e.target.value)}
        />
      </Field>
    </SettingsCard>
  )
}
