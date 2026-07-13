<#
Edge-case bulk test for Sevi / CvSU chatbot API.
Queries are drawn from patterns observed in CvSU student forums, Facebook groups,
and admission inquiry threads — covering phrasing variations, Taglish, typos,
campus-specific questions, and boundary inputs.

Usage:
  .\bulk_test_edge_cases.ps1 -TotalRequests 10000 -BatchSize 50
#>

param(
    [int]$TotalRequests  = 10000,
    [string]$ApiUrl      = "http://127.0.0.1:8001/batch",
    [int]$BatchSize      = 50,
    [int]$DelayMs        = 30,
    [string]$ResultsFile = "edge_case_results.jsonl",
    [string]$SummaryFile = "edge_case_summary.json"
)

# ---------------------------------------------------------------------------
# Edge-case query pool — drawn from CvSU forum/Facebook inquiry patterns
# ---------------------------------------------------------------------------
$edgeCases = @(
    # --- ADMISSIONS: standard ---
    "What are the requirements for CvSU admission?",
    "How do I apply to CvSU?",
    "What documents do I need to enroll at CvSU?",
    "Is there an entrance exam at CvSU?",
    "When is the application deadline for CvSU?",
    "Can I still apply to CvSU after the deadline?",
    "What is the cut-off score for CvSU admission exam?",
    "Does CvSU accept transferees?",
    "How do I transfer to CvSU from another school?",
    "What are the steps to apply as a transferee?",
    "Can I apply to CvSU without a Form 137?",
    "Is high school diploma required for CvSU admission?",
    "What grade average does CvSU require for admission?",
    "Does CvSU accept K-12 ALS graduates?",
    "Can a ladderized student enroll at CvSU?",

    # --- ADMISSIONS: Taglish / natural Filipino phrasing ---
    "Ano ang requirements para mag-apply sa CvSU?",
    "Kailan ang deadline ng application sa CvSU?",
    "Paano mag-apply sa CvSU?",
    "May entrance exam ba sa CvSU?",
    "Pwede ba mag-apply kahit late na?",
    "Ano ang cut-off score sa CvSU entrance exam?",
    "Tanggap ba ang transferee sa CvSU?",
    "Paano mag-transfer sa CvSU?",
    "Kailangan ba ng Form 137 sa CvSU?",
    "Anong average ang kailangan para makapasok sa CvSU?",
    "Tanggap ba ng CvSU ang ALS passer?",
    "Pwede ba mag-enroll sa CvSU kahit walang diploma?",

    # --- ADMISSIONS: typos / abbreviations ---
    "cvsu addmission requirments",
    "CVSU entrace exam schedule",
    "cavite state university application form",
    "CvSu requirements for freshmen",
    "how to enroll in cvsu indang",
    "cvsu admission 2026",
    "cvsu application form download",
    "cvsu online application",
    "CvSU ETEEAP requirements",

    # --- TUITION & FEES ---
    "How much is tuition at CvSU?",
    "Is CvSU tuition free?",
    "What fees do I pay at CvSU?",
    "Does the free tuition law cover CvSU?",
    "What is RA 10931 and does it apply to CvSU?",
    "Are there miscellaneous fees at CvSU?",
    "How much are the miscellaneous fees in CvSU?",
    "Do I still pay anything if tuition is free?",
    "What is the total cost of attending CvSU per semester?",
    "Is the free tuition available for transferees?",
    "Does free tuition apply to graduate students?",
    "How much is the laboratory fee at CvSU?",
    "Is there a student ID fee at CvSU?",
    "Where do I pay my fees at CvSU?",
    "Can I pay CvSU fees online?",
    "Libre ba ang tuition sa CvSU?",
    "Magkano ang miscellaneous fees sa CvSU?",
    "May bayad pa rin ba kahit libre ang tuition?",
    "Saan nagbabayad ng fees sa CvSU?",
    "cvsu tuition fee 2026",
    "cvsu free tution",
    "cvsu fees for transferee",

    # --- SCHOLARSHIPS ---
    "What scholarships are available at CvSU?",
    "How do I apply for a scholarship at CvSU?",
    "Does CvSU offer CHED scholarship?",
    "Is there a DOST scholarship at CvSU?",
    "What is the CvSU scholarship grant?",
    "Can I get a scholarship as a transferee?",
    "What are the requirements for CvSU scholarship?",
    "Is there a full scholarship at CvSU?",
    "Does CvSU have a working student program?",
    "How do I apply for financial aid at CvSU?",
    "Is there a stipend with the CvSU scholarship?",
    "What GPA do I need to maintain my scholarship?",
    "Can I keep my scholarship if I shift courses?",
    "Paano mag-apply ng scholarship sa CvSU?",
    "May scholarship ba para sa transferee sa CvSU?",
    "Ano ang requirements ng scholarship sa CvSU?",
    "cvsu scholarship requirements 2026",
    "cvsu ched scholarship",
    "cvsu dost scholarship apply",

    # --- COURSES & PROGRAMS ---
    "What courses does CvSU offer?",
    "Does CvSU have a nursing program?",
    "Is there a Medical Technology course at CvSU?",
    "Does CvSU offer Criminology?",
    "What engineering courses are available at CvSU?",
    "Is Architecture offered at CvSU?",
    "Does CvSU have Accountancy?",
    "Is there a Tourism and Hospitality Management course at CvSU?",
    "What courses are available at CvSU Imus campus?",
    "What programs does CvSU Bacoor offer?",
    "Is BS Psychology available at CvSU?",
    "Does CvSU have a Law school?",
    "Is there an Education course at CvSU?",
    "Does CvSU offer Computer Science?",
    "Is Information Technology available at CvSU Naic?",
    "What STEM courses does CvSU offer?",
    "Does CvSU have a Business Administration program?",
    "Is Mass Communication offered at CvSU?",
    "Does CvSU offer Social Work?",
    "Is there an Agricultural Engineering program at CvSU?",
    "Anong kurso ang meron sa CvSU?",
    "May nursing ba sa CvSU?",
    "May criminology ba sa CvSU?",
    "Anong courses ang available sa CvSU Imus?",
    "cvsu courses list 2026",
    "cvsu bsit program",
    "cvsu bscs indang",
    "cvsu engineering programs",
    "cvsu accountancy",

    # --- GRADUATE PROGRAMS ---
    "Does CvSU offer graduate programs?",
    "What master's degrees are available at CvSU?",
    "Is there a PhD program at CvSU?",
    "How do I apply for a master's degree at CvSU?",
    "What are the requirements for graduate school at CvSU?",
    "Is there an MBA at CvSU?",
    "Does CvSU have Master of Arts in Education?",
    "Is the graduate program available online at CvSU?",
    "What is the tuition for graduate programs at CvSU?",
    "May graduate school ba sa CvSU?",
    "Paano mag-apply sa master's program ng CvSU?",
    "cvsu graduate programs 2026",
    "cvsu masters degree requirements",
    "cvsu phd program",

    # --- ENROLLMENT ---
    "When is enrollment at CvSU?",
    "How do I enroll at CvSU?",
    "What is the enrollment process at CvSU?",
    "Is online enrollment available at CvSU?",
    "What is the CvSU student portal?",
    "How do I access the CvSU student portal?",
    "I forgot my CvSU student portal password, what do I do?",
    "How do I register for subjects at CvSU?",
    "Can I enroll in CvSU without going to campus?",
    "What is the enrollment schedule for freshmen?",
    "When does the second semester enrollment start at CvSU?",
    "Is there a late enrollment at CvSU?",
    "What happens if I miss enrollment at CvSU?",
    "How do I add or drop a subject at CvSU?",
    "Can I take overload subjects at CvSU?",
    "Kailan ang enrollment sa CvSU?",
    "Paano mag-enroll sa CvSU?",
    "Online ba ang enrollment sa CvSU?",
    "Paano i-access ang student portal ng CvSU?",
    "Nakalimutan ko ang password ko sa CvSU portal, ano gagawin?",
    "Pwede bang mag-enroll online sa CvSU?",
    "Kailan magsisimula ang enrollment ng second semester?",
    "cvsu enrollment schedule 2026",
    "cvsu student portal login",
    "cvsu online enrollment steps",
    "cvsu late enrollment policy",
    "cvsu add drop subject",

    # --- ACADEMIC CALENDAR ---
    "When does the school year start at CvSU?",
    "What is the CvSU academic calendar 2026?",
    "When is the semester break at CvSU?",
    "When is graduation at CvSU?",
    "What are the holidays observed at CvSU?",
    "When does the first semester end at CvSU?",
    "What is the schedule for summer classes at CvSU?",
    "Is there a summer class at CvSU this year?",
    "When is the exam week at CvSU?",
    "Kailan nagsisimula ang school year sa CvSU?",
    "Kailan ang graduation sa CvSU 2026?",
    "Kailan ang sem break sa CvSU?",
    "May summer class ba sa CvSU?",
    "cvsu academic calendar 2026",
    "cvsu graduation schedule",
    "cvsu exam week schedule",
    "cvsu summer class 2026",

    # --- CAMPUS LOCATIONS & SPECIFIC CAMPUSES ---
    "Where is CvSU located?",
    "Where is the CvSU main campus?",
    "How do I get to CvSU Indang?",
    "Where is CvSU Imus campus?",
    "Where is CvSU Bacoor campus?",
    "What is the address of CvSU Naic?",
    "Where is CvSU Carmona campus?",
    "Is there a CvSU campus in Silang?",
    "How many campuses does CvSU have?",
    "Which CvSU campus is nearest to Tagaytay?",
    "Is there a CvSU campus in Trece Martires?",
    "How far is CvSU from Manila?",
    "What is the CvSU Rosario campus address?",
    "Is there a CvSU campus in Tanza?",
    "How do I get to CvSU main campus by public transport?",
    "Saan naroon ang CvSU main campus?",
    "Paano pumunta sa CvSU Indang?",
    "Ilang campus ang mayroon ang CvSU?",
    "Saan ang pinaka-malapit na CvSU campus sa Tagaytay?",
    "cvsu indang address",
    "cvsu imus campus location",
    "cvsu bacoor campus",
    "cvsu naic campus",
    "cvsu carmona campus courses",
    "cvsu silang campus",
    "cvsu rosario campus",
    "cvsu trece campus",

    # --- FACILITIES ---
    "Does CvSU have a library?",
    "What are the library hours at CvSU?",
    "Does CvSU have a dormitory?",
    "How do I apply for CvSU dormitory?",
    "Is there a canteen at CvSU?",
    "Does CvSU have a gymnasium?",
    "Is there a computer lab at CvSU?",
    "Does CvSU have Wi-Fi on campus?",
    "Is there a school clinic at CvSU?",
    "Does CvSU have a chapel?",
    "Is there a sports facility at CvSU?",
    "Does CvSU have a swimming pool?",
    "Is there parking at CvSU?",
    "May dorm ba sa CvSU?",
    "Paano mag-apply ng dorm sa CvSU?",
    "May libreng Wi-Fi ba sa CvSU campus?",
    "May klinikal ba sa CvSU?",
    "cvsu dormitory application",
    "cvsu library hours",
    "cvsu facilities list",
    "cvsu wifi campus",

    # --- REGISTRAR ---
    "How do I get my transcript of records at CvSU?",
    "How long does it take to get a TOR from CvSU?",
    "How do I request a diploma from CvSU?",
    "Where is the CvSU registrar office?",
    "What are the requirements for requesting a TOR?",
    "Can I request my TOR online at CvSU?",
    "How much does a CvSU transcript cost?",
    "How do I get a certificate of enrollment from CvSU?",
    "Can I get my CvSU records if I have unpaid balance?",
    "How do I get a certified true copy of my grades?",
    "What are the registrar office hours at CvSU?",
    "Paano makuha ang transcript sa CvSU?",
    "Gaano katagal makuha ang TOR sa CvSU?",
    "Paano mag-request ng diploma sa CvSU?",
    "Saan ang registrar ng CvSU?",
    "Pwede bang i-request ang TOR online sa CvSU?",
    "cvsu tor request requirements",
    "cvsu transcript of records how to get",
    "cvsu diploma request",
    "cvsu certificate of enrollment",
    "cvsu registrar contact",

    # --- CONTACT INFO ---
    "What is the CvSU contact number?",
    "What is the email address of CvSU?",
    "How do I contact CvSU admissions office?",
    "What is the phone number of CvSU registrar?",
    "What is the CvSU official website?",
    "How do I reach CvSU main campus by phone?",
    "What is the email for CvSU scholarship office?",
    "How do I contact the CvSU guidance office?",
    "Is there a Facebook page for CvSU?",
    "What is the CvSU official email?",
    "Ano ang contact number ng CvSU?",
    "Paano makipag-ugnayan sa CvSU admissions?",
    "Ano ang email ng CvSU registrar?",
    "cvsu contact number indang",
    "cvsu email address admissions",
    "cvsu official website",
    "cvsu facebook page",

    # --- STUDENT LIFE & ORGANIZATIONS ---
    "What student organizations are there at CvSU?",
    "How do I join a student organization at CvSU?",
    "Is there a student council at CvSU?",
    "Does CvSU have a journalism or media organization?",
    "Is there a debate club at CvSU?",
    "What sports teams does CvSU have?",
    "Does CvSU have a band or choir?",
    "Is there a ROTC program at CvSU?",
    "Does CvSU celebrate Intramurals?",
    "What is the CvSU KASAMA organization?",
    "May student council ba sa CvSU?",
    "Paano sumali sa org sa CvSU?",
    "May sports team ba ang CvSU?",
    "May ROTC ba sa CvSU?",
    "cvsu student organizations list",
    "cvsu intramurals schedule",
    "cvsu student council",
    "cvsu rotc",

    # --- ABOUT CvSU / VISION-MISSION ---
    "What is CvSU known for?",
    "When was CvSU established?",
    "What is the history of CvSU?",
    "Who is the president of CvSU?",
    "What is the CvSU motto?",
    "What is the mission of CvSU?",
    "What is the vision of CvSU?",
    "What awards has CvSU received?",
    "Is CvSU accredited?",
    "What is CvSU's AACCUP rating?",
    "Is CvSU a center of excellence?",
    "How many students does CvSU have?",
    "Kailan naitayo ang CvSU?",
    "Sino ang presidente ng CvSU?",
    "Ano ang motto ng CvSU?",
    "Ano ang misyon ng CvSU?",
    "cvsu history founding",
    "cvsu president 2026",
    "cvsu accreditation status",
    "cvsu center of excellence",

    # --- EVENTS ---
    "What events does CvSU host?",
    "When is the CvSU foundation day?",
    "Is there a career fair at CvSU?",
    "Does CvSU have a job fair?",
    "When is the CvSU research symposium?",
    "What is the CvSU lantern parade?",
    "Does CvSU have a cultural festival?",
    "Is there a graduation ball at CvSU?",
    "Kailan ang foundation day ng CvSU?",
    "May job fair ba sa CvSU?",
    "cvsu foundation day 2026",
    "cvsu job fair schedule",
    "cvsu research symposium",
    "cvsu cultural events",

    # --- EDGE: very short queries ---
    "tuition?",
    "enrollment?",
    "scholarship?",
    "TOR?",
    "admission?",
    "courses?",
    "dorm?",
    "library?",
    "registrar?",
    "contact?",
    "campus?",
    "exam?",
    "fees?",
    "apply?",
    "graduate?",

    # --- EDGE: very long / run-on queries ---
    "I am a Grade 12 student from a public school in Cavite and I want to know if I can apply to CvSU Indang for Computer Science and what documents I need to prepare and when exactly is the application period and if there is an entrance exam I need to take",
    "My daughter wants to transfer from a private university to CvSU because the tuition is too expensive and we would like to know if transferees are accepted and what the requirements are and how long the processing usually takes",
    "I already passed the CvSU entrance exam last semester but I was not able to enroll due to personal reasons can I still use my exam result this coming enrollment period or do I need to take the exam again",
    "Is it true that CvSU tuition is completely free under RA 10931 and do I still need to pay anything like laboratory fees or miscellaneous fees and where do I pay those and can I pay online",
    "I want to apply for a scholarship at CvSU but I do not know which ones are available and what the GWA requirement is and whether I can still get a scholarship even if I am already enrolled",

    # --- EDGE: ambiguous / off-topic ---
    "help",
    "hello",
    "hi there",
    "what can you do?",
    "are you a robot?",
    "thank you",
    "thanks",
    "ok",
    "bye",
    "good morning",
    "I need help",
    "can you help me?",
    "what is CvSU?",
    "tell me everything about CvSU",
    "I don't understand",
    "how does this work?",

    # --- EDGE: mixed language / Taglish boundary ---
    "Pwede ba akong mag-apply sa CvSU kahit Grade 11 pa lang ako?",
    "Ilan ang units na kailangan ko para mag-graduate sa CvSU?",
    "Paano makita ang grades ko sa CvSU student portal?",
    "May shifting ba sa CvSU? Gusto ko pong magpalit ng course.",
    "Kailangan ko ba ng NBI clearance para sa CvSU admission?",
    "Magkano ang CvSU miscellaneous fee para sa engineering students?",
    "Anong requirements ang kailangan para sa CvSU graduate school?",
    "Bakit hindi ako makapag-login sa CvSU student portal?",
    "May NSTP ba sa CvSU? Paano pumili ng component?",
    "Paano makuha ang CvSU ID pagkatapos ng enrollment?",

    # --- EDGE: specific / niche questions ---
    "Does CvSU accept foreign students?",
    "Can international students apply to CvSU?",
    "Is there a foreign student admission process at CvSU?",
    "Does CvSU offer ladderized engineering programs?",
    "Can a PWD student apply for accommodation at CvSU?",
    "Is CvSU a member of CHED?",
    "Does CvSU have a research extension program?",
    "Is CvSU participating in the K-12 transition program?",
    "Does CvSU have a linkage with foreign universities?",
    "Can I take elective subjects outside my course at CvSU?",
    "Is there cross-enrollment allowed at CvSU?",
    "Does CvSU have an ETEEAP program?",
    "What is the CvSU ETEEAP and how do I apply?",
    "Is there an online degree program at CvSU?",
    "Does CvSU offer distance learning?",
    "Can I take a second degree at CvSU?",
    "Is there a bridging program for non-engineering graduates at CvSU?",
    "Does CvSU accept homeschool graduates?",
    "What is the CvSU retention policy?",
    "What happens if I fail a subject at CvSU?",
    "How many times can I re-take a failed subject at CvSU?",
    "Is there academic probation at CvSU?",
    "What is the CvSU grading system?",
    "How does CvSU compute the GWA?",
    "What grade is needed to pass a subject at CvSU?",
    "Is 3.0 a passing grade at CvSU?",
    "What is the incomplete grade policy at CvSU?",
    "How do I remove an incomplete grade at CvSU?",
    "Can I take a leave of absence at CvSU?",
    "What is the CvSU leave of absence policy?"
)

# ---------------------------------------------------------------------------
# Variation helpers — replicate the organic phrasing diversity seen in forums
# ---------------------------------------------------------------------------
$prefixes = @(
    "Can you tell me ",
    "I want to know ",
    "Please help me with ",
    "I have a question about ",
    "Can I ask about ",
    "Sana matulungan mo ako sa ",
    "Gusto ko pong malaman ang ",
    "Quick question - ",
    "Hi, I just want to ask - "
)

$suffixes = @(
    " Thanks!",
    " Please answer ASAP.",
    " It's urgent.",
    " for freshmen?",
    " for 2026?",
    " for CvSU Indang?",
    " for CvSU Imus?",
    " for CvSU Bacoor?",
    " po?",
    " please?",
    " ASAP po"
)

function Get-EdgeQuery {
    $base = Get-Random -InputObject $script:edgeCases

    # 40% chance to prepend a prefix, 30% chance to append a suffix
    $query = $base
    if ((Get-Random -Minimum 0 -Maximum 10) -lt 4) {
        $query = (Get-Random -InputObject $script:prefixes) + $query
    }
    if ((Get-Random -Minimum 0 -Maximum 10) -lt 3) {
        $query = $query + (Get-Random -InputObject $script:suffixes)
    }
    return $query.Trim()
}

# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------
if (Test-Path $ResultsFile) { Remove-Item $ResultsFile -Force }
if (Test-Path $SummaryFile) { Remove-Item $SummaryFile -Force }

$stats = @{ SuccessCount = 0; ErrorCount = 0; ConfidenceSum = 0.0; IntentCounts = @{} }

function New-BatchRequests($startIndex, $count) {
    $items = @()
    for ($i = 0; $i -lt $count; $i++) {
        $idx  = $startIndex + $i + 1
        $user = [math]::Floor(($idx - 1) / 5) + 1
        $items += [PSCustomObject]@{
            Index     = $idx
            Query     = Get-EdgeQuery
            UserId    = "edge_user_$user"
            SessionId = "edge_session_$user"
        }
    }
    return $items
}

function Write-JsonLines($records) {
    $lines = $records | ForEach-Object { $_ | ConvertTo-Json -Depth 4 -Compress }
    Add-Content -Path $ResultsFile -Value $lines -Encoding utf8
}

function Update-Stats($records) {
    foreach ($r in $records) {
        if ([string]::IsNullOrWhiteSpace($r.Error)) {
            $stats.SuccessCount++
            $stats.ConfidenceSum += [double]$r.Confidence
            if (-not $stats.IntentCounts.ContainsKey($r.Intent)) { $stats.IntentCounts[$r.Intent] = 0 }
            $stats.IntentCounts[$r.Intent]++
        } else { $stats.ErrorCount++ }
    }
}

function Send-BatchRequest($batchNum, $batchItems) {
    $payload = @(foreach ($item in $batchItems) {
        @{ message = $item.Query; user_id = $item.UserId; session_id = $item.SessionId }
    }) | ConvertTo-Json -Depth 5

    try {
        $resp = Invoke-RestMethod -Uri $ApiUrl -Method Post -ContentType "application/json" -Body $payload -TimeoutSec 60
        if (-not $resp.results) { throw "No results array returned." }

        $records = @()
        for ($i = 0; $i -lt $batchItems.Count; $i++) {
            $item = $batchItems[$i]; $res = $resp.results[$i]
            $conf = if ($res.PSObject.Properties.Name -contains "confidence" -and $null -ne $res.confidence) { [double]$res.confidence } else { 0 }
            $records += [PSCustomObject]@{
                Timestamp  = (Get-Date).ToString("o")
                Batch      = $batchNum
                Index      = $item.Index
                Query      = $item.Query
                UserId     = $item.UserId
                SessionId  = $item.SessionId
                Intent     = if ($res.PSObject.Properties.Name -contains "intent") { $res.intent } else { "" }
                Confidence = $conf
                Response   = if ($res.PSObject.Properties.Name -contains "response") { $res.response } else { "" }
                Error      = ""
            }
        }
        Write-JsonLines $records; Update-Stats $records; return $records
    } catch {
        $records = @()
        foreach ($item in $batchItems) {
            $records += [PSCustomObject]@{
                Timestamp = (Get-Date).ToString("o"); Batch = $batchNum; Index = $item.Index
                Query = $item.Query; UserId = $item.UserId; SessionId = $item.SessionId
                Intent = ""; Confidence = 0; Response = ""; Error = $_.Exception.Message
            }
        }
        Write-JsonLines $records; Update-Stats $records; return $records
    }
}

$startedAt  = Get-Date
$batchCount = [math]::Ceiling($TotalRequests / $BatchSize)
Write-Host "Edge-case test starting: $TotalRequests requests to $ApiUrl  (pool: $($edgeCases.Count) unique queries)"

for ($b = 0; $b -lt $batchCount; $b++) {
    $startIdx = $b * $BatchSize
    $size     = [math]::Min($BatchSize, $TotalRequests - $startIdx)
    Send-BatchRequest ($b + 1) (New-BatchRequests $startIdx $size) | Out-Null

    $done = $startIdx + $size
    if ($done % 1000 -eq 0 -or $done -eq $TotalRequests) { Write-Host "Processed $done / $TotalRequests" }
    if ($b -lt ($batchCount - 1)) { Start-Sleep -Milliseconds $DelayMs }
}

$elapsed = ((Get-Date) - $startedAt).TotalSeconds
$avgConf  = if ($stats.SuccessCount -gt 0) { $stats.ConfidenceSum / $stats.SuccessCount } else { 0 }

$intentSummary = $stats.IntentCounts.GetEnumerator() |
    Sort-Object Value -Descending |
    ForEach-Object { [PSCustomObject]@{ Intent = $_.Key; Count = $_.Value } }

$fallbackCount = ($stats.IntentCounts["nlu_fallback"] -as [int])
$fallbackRate  = if ($stats.SuccessCount -gt 0) { [math]::Round($fallbackCount / $stats.SuccessCount * 100, 2) } else { 0 }

$summary = [PSCustomObject]@{
    AssistantName    = "Sevi"
    ApiUrl           = $ApiUrl
    TotalRequests    = $TotalRequests
    BatchSize        = $BatchSize
    SuccessCount     = $stats.SuccessCount
    ErrorCount       = $stats.ErrorCount
    AverageConfidence = [math]::Round($avgConf, 6)
    FallbackCount    = $fallbackCount
    FallbackRate     = "$fallbackRate%"
    DurationSeconds  = [math]::Round($elapsed, 2)
    ResultsFile      = $ResultsFile
    Intents          = $intentSummary
}

$summary | ConvertTo-Json -Depth 5 | Set-Content -Path $SummaryFile -Encoding utf8

Write-Host "`nEdge-case test complete."
Write-Host "Successful: $($stats.SuccessCount)  |  Errors: $($stats.ErrorCount)"
Write-Host "Avg confidence: $([math]::Round($avgConf * 100, 2))%"
Write-Host "Fallback rate: $fallbackRate%"
Write-Host "Summary: $SummaryFile"
