<#
Batch bulk test script for Sevi / CvSU chatbot API
Usage:
  py -3.11 -m uvicorn app:app --host 127.0.0.1 --port 8001
  .\bulk_test_requests_batch.ps1 -TotalRequests 5000 -BatchSize 25
#>

param(
    [int]$TotalRequests = 5000,
    [string]$ApiUrl = "http://127.0.0.1:8001/batch",
    [int]$BatchSize = 25,
    [int]$DelayMs = 50,
    [string]$ResultsFile = "bulk_test_batch_results.jsonl",
    [string]$SummaryFile = "bulk_test_batch_summary.json"
)

$baseQueries = @(
    "What are the admission requirements",
    "How much is tuition",
    "Does CvSU offer Computer Science",
    "Are there scholarships available",
    "Where is CvSU located",
    "What facilities does CvSU have",
    "How do I contact admissions",
    "When is enrollment",
    "What courses are offered",
    "Tell me about campus life",
    "What are the library hours",
    "How do I join a student organization",
    "What is the academic calendar",
    "How do I register for classes",
    "What is CvSU's vision and mission",
    "Where can I get my transcript",
    "What graduate programs are available",
    "How do I apply for financial aid",
    "What student services are available",
    "How do I find the registrar office",
    "What are the dormitory options",
    "Is there a campus shuttle service",
    "What are the admission exam dates",
    "Can I transfer from another university",
    "How do I apply for a scholarship",
    "What are the course requirements for engineering",
    "Does CvSU offer online classes",
    "What is the student portal",
    "How do I request a diploma copy",
    "What is the school calendar for next semester",
    "Does CvSU have a guidance office",
    "Are there internship programs",
    "What are the class schedules",
    "How do I pay my fees",
    "What are the laboratory facilities",
    "Is there a school clinic on campus",
    "How do I join the student council",
    "Where can I find campus maps",
    "What are the library borrowing rules",
    "Does CvSU have a sports program",
    "How can I contact the registrar"
)

$phrases = @(
    "?",
    " today?",
    " this year?",
    " for new students?",
    " for freshmen?",
    " in 2026?",
    " right now?",
    " as a transfer student?",
    " for international students?",
    " for the main campus?",
    " by email?",
    " by phone?"
)

$modifiers = @(
    "Please tell me",
    "Can you explain",
    "I want to know",
    "Help me understand",
    "Give me details on",
    "I'm asking about",
    "Share information on",
    "I need to know",
    "Tell me about"
)

if (Test-Path $ResultsFile) { Remove-Item $ResultsFile -Force }
if (Test-Path $SummaryFile) { Remove-Item $SummaryFile -Force }

$stats = @{
    SuccessCount = 0
    ErrorCount = 0
    ConfidenceSum = 0.0
    IntentCounts = @{}
}

function Get-RandomQuery {
    $base = Get-Random -InputObject $baseQueries
    $phrase = Get-Random -InputObject $phrases
    $prefix = Get-Random -InputObject $modifiers

    if ((Get-Random -Minimum 0 -Maximum 2) -eq 0) {
        return "$prefix $base$phrase"
    }

    return "$base$phrase"
}

function New-BatchRequests($startIndex, $count) {
    $items = @()

    for ($offset = 0; $offset -lt $count; $offset++) {
        $requestIndex = $startIndex + $offset + 1
        $syntheticUser = [math]::Floor(($requestIndex - 1) / 5) + 1

        $items += [PSCustomObject]@{
            Index = $requestIndex
            Query = Get-RandomQuery
            UserId = "batch_user_$syntheticUser"
            SessionId = "batch_session_$syntheticUser"
        }
    }

    return $items
}

function Write-JsonLines($records) {
    $jsonLines = $records | ForEach-Object {
        $_ | ConvertTo-Json -Depth 6 -Compress
    }

    Add-Content -Path $ResultsFile -Value $jsonLines -Encoding utf8
}

function Update-Stats($records) {
    foreach ($record in $records) {
        if ([string]::IsNullOrWhiteSpace($record.Error)) {
            $stats.SuccessCount++
            $stats.ConfidenceSum += [double]$record.Confidence

            if (-not $stats.IntentCounts.ContainsKey($record.Intent)) {
                $stats.IntentCounts[$record.Intent] = 0
            }

            $stats.IntentCounts[$record.Intent]++
        }
        else {
            $stats.ErrorCount++
        }
    }
}

function Send-BatchRequest($batchNumber, $batchRequests) {
    $payload = @(
        foreach ($request in $batchRequests) {
            @{
                message = $request.Query
                user_id = $request.UserId
                session_id = $request.SessionId
            }
        }
    ) | ConvertTo-Json -Depth 5

    try {
        $response = Invoke-RestMethod -Uri $ApiUrl -Method Post -ContentType "application/json" -Body $payload -TimeoutSec 60

        if (-not $response.results) {
            throw "Batch endpoint did not return a results array."
        }

        if ($response.results.Count -ne $batchRequests.Count) {
            throw "Batch result count mismatch. Expected $($batchRequests.Count), got $($response.results.Count)."
        }

        $records = @()

        for ($i = 0; $i -lt $batchRequests.Count; $i++) {
            $request = $batchRequests[$i]
            $result = $response.results[$i]

            $confidence = 0
            if ($result.PSObject.Properties.Name -contains "confidence" -and $null -ne $result.confidence) {
                $confidence = [double]$result.confidence
            }

            $records += [PSCustomObject]@{
                Timestamp  = (Get-Date).ToString("o")
                Batch      = $batchNumber
                Index      = $request.Index
                Query      = $request.Query
                UserId     = $request.UserId
                SessionId  = $request.SessionId
                Intent     = if ($result.PSObject.Properties.Name -contains "intent") { $result.intent } else { "" }
                Confidence = $confidence
                Response   = if ($result.PSObject.Properties.Name -contains "response") { $result.response } else { "" }
                Error      = ""
            }
        }

        Write-JsonLines $records
        Update-Stats $records
        return $records
    }
    catch {
        $records = @()

        foreach ($request in $batchRequests) {
            $records += [PSCustomObject]@{
                Timestamp  = (Get-Date).ToString("o")
                Batch      = $batchNumber
                Index      = $request.Index
                Query      = $request.Query
                UserId     = $request.UserId
                SessionId  = $request.SessionId
                Intent     = ""
                Confidence = 0
                Response   = ""
                Error      = $_.Exception.Message
            }
        }

        Write-JsonLines $records
        Update-Stats $records
        return $records
    }
}

$startedAt = Get-Date
Write-Host "Batch bulk test starting for Sevi: $TotalRequests requests to $ApiUrl"
Write-Host "Results file: $ResultsFile"
Write-Host "Summary file: $SummaryFile"

$batchCount = [math]::Ceiling($TotalRequests / $BatchSize)
for ($batch = 0; $batch -lt $batchCount; $batch++) {
    $startIndex = $batch * $BatchSize
    $remaining = $TotalRequests - $startIndex
    $currentBatchSize = [math]::Min($BatchSize, $remaining)
    $batchRequests = New-BatchRequests $startIndex $currentBatchSize

    Send-BatchRequest ($batch + 1) $batchRequests | Out-Null

    $processed = $startIndex + $currentBatchSize
    if ($processed % 1000 -eq 0 -or $processed -eq $TotalRequests) {
        Write-Host "Processed $processed / $TotalRequests"
    }

    if ($batch -lt ($batchCount - 1)) {
        Start-Sleep -Milliseconds $DelayMs
    }
}

$duration = ((Get-Date) - $startedAt).TotalSeconds
$averageConfidence = 0
if ($stats.SuccessCount -gt 0) {
    $averageConfidence = $stats.ConfidenceSum / $stats.SuccessCount
}

$intentSummary = foreach ($intent in ($stats.IntentCounts.Keys | Sort-Object)) {
    [PSCustomObject]@{
        Intent = $intent
        Count = $stats.IntentCounts[$intent]
    }
}

$summary = [PSCustomObject]@{
    AssistantName = "Sevi"
    ApiUrl = $ApiUrl
    TotalRequests = $TotalRequests
    BatchSize = $BatchSize
    DelayMs = $DelayMs
    SuccessCount = $stats.SuccessCount
    ErrorCount = $stats.ErrorCount
    AverageConfidence = [math]::Round($averageConfidence, 6)
    DurationSeconds = [math]::Round($duration, 2)
    ResultsFile = $ResultsFile
    Intents = $intentSummary
}

$summary | ConvertTo-Json -Depth 5 | Set-Content -Path $SummaryFile -Encoding utf8

Write-Host "Batch bulk test complete for Sevi."
Write-Host "Successful responses: $($stats.SuccessCount)"
Write-Host "Errors: $($stats.ErrorCount)"
Write-Host "Average confidence: $([math]::Round($averageConfidence * 100, 2))%"
Write-Host "Summary saved to $SummaryFile"
