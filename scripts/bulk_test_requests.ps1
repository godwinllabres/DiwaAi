<#
Bulk test script for CvSU chatbot API
Usage:
  py -3.11 -m uvicorn app:app --host 127.0.0.1 --port 8001
  .\bulk_test_requests.ps1 -TotalRequests 1000000
#>

param(
    [int]$TotalRequests = 1000000,
    [string]$ApiUrl = "http://127.0.0.1:8001/chat",
    [int]$BatchSize = 100,
    [int]$DelayMs = 25
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

$resultsFile = "bulk_test_results.jsonl"
if (Test-Path $resultsFile) { Remove-Item $resultsFile -Force }

function Get-RandomQuery {
    $base = Get-Random -InputObject $baseQueries
    $phrase = Get-Random -InputObject $phrases
    $prefix = Get-Random -InputObject $modifiers

    if ((Get-Random -Minimum 0 -Maximum 2) -eq 0) {
        return "$prefix $base$phrase"
    }
    else {
        return "$base$phrase"
    }
}

function Send-Request($index, $query) {
    $payload = @{
        message = $query
        user_id = "bulk_test_$index"
    } | ConvertTo-Json

    try {
        $response = Invoke-RestMethod -Uri $ApiUrl -Method Post -ContentType "application/json" -Body $payload -TimeoutSec 30
        $result = [PSCustomObject]@{
            Index      = $index
            Query      = $query
            Intent     = $response.intent
            Confidence = $response.confidence
            Response   = $response.response
            ModelUsed  = if ($response.PSObject.Properties.Name -contains 'model_used') { $response.model_used } else { '' }
            Error      = ''
        }
        $result | ConvertTo-Json -Depth 5 | Out-File -FilePath $resultsFile -Append -Encoding utf8
        return $result
    }
    catch {
        $errorResult = [PSCustomObject]@{
            Index      = $index
            Query      = $query
            Intent     = ''
            Confidence = 0
            Response   = ''
            ModelUsed  = ''
            Error      = $_.Exception.Message
        }
        $errorResult | ConvertTo-Json -Depth 5 | Out-File -FilePath $resultsFile -Append -Encoding utf8
        return $errorResult
    }
}

Write-Host "Bulk request test starting: $TotalRequests requests to $ApiUrl"
Write-Host "Results will be appended to $resultsFile"

$batchCount = [math]::Ceiling($TotalRequests / $BatchSize)
for ($batch = 0; $batch -lt $batchCount; $batch++) {
    $start = $batch * $BatchSize
    $end = [math]::Min($start + $BatchSize - 1, $TotalRequests - 1)
    $batchRequests = @()

    for ($i = $start; $i -le $end; $i++) {
        $query = Get-RandomQuery
        $batchRequests += [PSCustomObject]@{
            Index = $i + 1
            Query = $query
        }
    }

    foreach ($item in $batchRequests) {
        Send-Request $item.Index $item.Query
        if ($item.Index % 1000 -eq 0) {
            Write-Host "Processed $($item.Index) / $TotalRequests"
        }
    }

    Start-Sleep -Milliseconds $DelayMs
}

Write-Host "Bulk request test complete. Output file: $resultsFile"
