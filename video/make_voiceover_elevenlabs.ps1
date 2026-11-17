# Generates the narration for Custody_Market_Monitor with ElevenLabs (Eleven v3, expressive).
# Needs an ElevenLabs API key: https://elevenlabs.io  -> Profile -> API keys (free tier is enough).
#   powershell -ExecutionPolicy Bypass -File .\video\make_voiceover_elevenlabs.ps1
#   ... -VoiceId <id>      use a different voice (default: George, warm British narrator)
#   ... -ListVoices        list the voices on your account
# The key is read from $env:ELEVENLABS_API_KEY, or ELEVENLABS_API_KEY=... in .env, or asked for.
param([string]$VoiceId = "JBFqnCBsd6RMkjVDRZzb", [switch]$ListVoices, [string]$Model = "eleven_v3")
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$key = $env:ELEVENLABS_API_KEY
if (-not $key -and (Test-Path (Join-Path $root ".env"))) {
  $line = Get-Content (Join-Path $root ".env") | Where-Object { $_ -match '^\s*ELEVENLABS_API_KEY\s*=' } | Select-Object -First 1
  if ($line) { $key = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'") }
}
if (-not $key) {
  $sec = Read-Host "ElevenLabs API key" -AsSecureString
  $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
$headers = @{ "xi-api-key" = $key }
if ($ListVoices) {
  (Invoke-RestMethod -Uri "https://api.elevenlabs.io/v1/voices" -Headers $headers).voices |
    ForEach-Object { "{0}  {1}  {2}" -f $_.voice_id, $_.name, ($_.labels | ConvertTo-Json -Compress) }
  exit
}
$out = Join-Path $PSScriptRoot "vo_el"
New-Item -ItemType Directory -Force -Path $out | Out-Null

# Eleven v3 reads the [bracketed] audio tags as delivery directions; they are not spoken.
$lines = [ordered]@{
 "00_title"      = "[warm] Custody Market Monitor. [confident] Who services America's forty-two-trillion-dollar fund industry... and who is winning?"
 "01_overview"   = "State Street is the number-one US fund custodian, at thirty-seven point eight percent. [serious] But its share has slipped six point four points since fiscal twenty twenty-two."
 "02_market"     = "Market share can be cut any way you need: by service, by fiscal year, and by fund type."
 "03_switches"   = "The Switches view tracks every provider change. [serious] Since fiscal twenty twenty-three, State Street has lost one point five five trillion dollars of custody assets, mostly BlackRock's iShares."
 "04_families"   = "Fund Families shows any client's complete provider map. [reassuring] At BlackRock, State Street is still the largest custodian."
 "05_prospects"  = "[upbeat] Prospects turns that into a sales list: sixty large families where State Street holds less than half of custody. Twelve already use State Street for another service."
 "06_chat_title" = "[curious] Now, the analyst. Just ask, in plain English."
 "07_ask"        = "Who has State Street lost custody business to... and how much?"
 "08_answer"     = "The answer leads with the headline: JPMorgan, BNY and Citi took about one point six five trillion dollars, driven mainly by a single client. Then come the supporting tables."
 "09_caveats"    = "[thoughtful] It also flags its own caveats: concentration, incomplete years, and reporting lag."
 "10_working"    = "And every number is traceable. Show working lists each data lookup behind the answer."
 "11_followup"   = "Follow-up questions keep the context."
 "12_followans"  = "[impressed] In seconds, a market question becomes an account view. State Street remains BlackRock's largest custodian, and a major administrator and transfer agent. One click opens the full profile."
 "13_close"      = "[warm] Custody Market Monitor. Market share, switches, prospects... and an analyst you can question. All built on public SEC filings."
}

function Speak($text, $model, $path) {
  $settings = if ($model -eq "eleven_v3") { @{ stability = 0.5 } } else { @{ stability = 0.45; similarity_boost = 0.8; style = 0.35; use_speaker_boost = $true } }
  $body = @{ text = $text; model_id = $model; voice_settings = $settings } | ConvertTo-Json -Depth 4
  Invoke-RestMethod -Method Post -Uri "https://api.elevenlabs.io/v1/text-to-speech/$VoiceId`?output_format=mp3_44100_128" `
    -Headers $headers -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body)) -OutFile $path
}

foreach ($k in $lines.Keys) {
  $path = Join-Path $out ($k + ".mp3")
  try {
    Speak $lines[$k] $Model $path
  } catch {
    Write-Warning "$Model failed for $k ($($_.Exception.Message)); retrying with eleven_multilingual_v2 without tags"
    Speak (($lines[$k] -replace '\[[^\]]+\]\s*', '')) "eleven_multilingual_v2" $path
  }
  Write-Host "wrote $path"
}
Write-Host "Done. Tell Claude the clips are in video\vo_el."
