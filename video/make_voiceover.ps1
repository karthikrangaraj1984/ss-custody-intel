# Generates the narration for Custody_Market_Monitor.mp4 using a voice built into Windows.
#   powershell -ExecutionPolicy Bypass -File .\video\make_voiceover.ps1            (default voice)
#   powershell -ExecutionPolicy Bypass -File .\video\make_voiceover.ps1 -List      (show installed voices)
#   powershell -ExecutionPolicy Bypass -File .\video\make_voiceover.ps1 -Voice "Microsoft Zira Desktop"
param([string]$Voice = "", [int]$Rate = 0, [switch]$List)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($List) { $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name + "  (" + $_.VoiceInfo.Gender + ", " + $_.VoiceInfo.Culture + ")" }; exit }
if ($Voice) { $s.SelectVoice($Voice) }
elseif ($s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Name -like "*Zira*" }) { $s.SelectVoice(($s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Name -like "*Zira*" } | Select-Object -First 1).VoiceInfo.Name) }
$s.Rate = $Rate
$out = Join-Path $PSScriptRoot "vo"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$lines = [ordered]@{
 "00_title"      = "Custody Market Monitor. Who services America's forty-two trillion dollar fund industry, and who is winning."
 "01_overview"   = "State Street is the number one US fund custodian, at thirty-seven point eight percent. But its share is down six point four points since fiscal twenty twenty-two."
 "02_market"     = "Market share can be cut by any service, any fiscal year, and any fund type."
 "03_switches"   = "The switches view tracks every provider change. Since fiscal twenty twenty-three, State Street lost one point five five trillion dollars of custody assets, mostly BlackRock's iShares."
 "04_families"   = "Fund families shows any client's full provider map. At BlackRock, State Street is still the largest custodian."
 "05_prospects"  = "Prospects ranks sixty large families where State Street holds less than half of custody. Twelve already use State Street for another service."
 "06_chat_title" = "Now, the analyst. Ask questions in plain English."
 "07_ask"        = "Who has State Street lost custody business to, and how much?"
 "08_answer"     = "The answer leads with the headline. JPMorgan, BNY and Citi took about one point six five trillion dollars, driven mainly by one client. The supporting tables follow."
 "09_caveats"    = "It flags its own caveats: concentration, incomplete years, and reporting lag."
 "10_working"    = "And every number is traceable. Show working lists each data lookup behind the answer."
 "11_followup"   = "Follow-up questions keep the context."
 "12_followans"  = "In seconds, a market question becomes an account view. State Street remains BlackRock's largest custodian, and a major administrator and transfer agent. One click opens the full profile."
 "13_close"      = "Custody Market Monitor. Market share, switches, prospects, and an analyst you can question, all built on public SEC filings."
}
foreach ($k in $lines.Keys) {
  $path = Join-Path $out ($k + ".wav")
  $s.SetOutputToWaveFile($path, $fmt)
  $s.Speak($lines[$k])
  $s.SetOutputToNull()
  Write-Host "wrote $path"
}
Write-Host "Voice used: $($s.Voice.Name)"
