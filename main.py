import os
import datetime
import json
import logging
import traceback
import webbrowser
import requests
import argparse
import openai
import anthropic
import psutil
from dotenv import load_dotenv
import urllib.parse
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

# Set up logging
logging.basicConfig(filename='ai_process_report.log', level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
load_dotenv()

# Create a session for persistent HTTP connections
http_session = requests.Session()

console = Console()

PROCESS_LIMIT = int(os.getenv("PROCESS_LIMIT", 200))
AI_PROVIDER = os.getenv('AI_PROVIDER', 'ollama').lower()

def get_processes():
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]Gathering processes...", total=None)
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'exe', 'status', 'username']):
            try:
                info = {
                    'pid': proc.info['pid'],
                    'name': proc.info['name'],
                    'exe': proc.info['exe'] or 'Unknown',
                    'status': proc.info['status'],
                    'username': proc.info['username'] or 'Unknown',
                    'cpu_percent': proc.cpu_percent(interval=0.1),
                    'memory_percent': proc.memory_percent()
                }
                processes.append(info)
                if len(processes) >= PROCESS_LIMIT:
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
                logging.warning(f"Skipping process due to: {e}")
            except Exception as e:
                logging.error(f"Unexpected error when getting process info: {e}")
        progress.update(task, completed=100)
    return processes

def save_processes_to_file(processes):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"processes_{timestamp}.json"
    with open(filename, 'w') as f:
        json.dump(processes, f)
    return filename

def analyze_processes_anthropic(processes_file):
    client = anthropic.Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        default_headers={"anthropic-beta": "max-tokens-3-5-sonnet-2024-07-15"}
    )
    model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20240620")
    
    with open(processes_file, 'r') as f:
        processes = json.load(f)
    
    prompt = f"""Analyze the following list of Windows processes:

{json.dumps(processes, indent=2)}

For each process, provide a brief description of its typical function and assign a threat score from 0 (harmless) to 10 (highly suspicious). If you're uncertain about a process, state that clearly.

Format your response as follows for each process:
Process Name: [name]
Description: [brief description]
Threat Score: [score]

Do not include any other text or formatting."""

    with console.status(f"[bold green]Analyzing processes with Anthropic ({model})..."):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )
            
            logging.debug(f"Anthropic response tokens: input={response.usage.input_tokens}, output={response.usage.output_tokens}")
            
            # Access the content of the response
            content = response.content
            
            # If content is a list of TextBlock objects, extract the text
            if isinstance(content, list) and all(hasattr(item, 'text') for item in content):
                content = '\n'.join(item.text for item in content)
            
            # Parse the response content
            analysis = {}
            current_process = None
            description = []
            
            for line in content.split('\n'):
                if line.startswith("Process Name:"):
                    if current_process:
                        analysis[current_process]['description'] = ' '.join(description)
                    current_process = line.split(":")[1].strip()
                    analysis[current_process] = {}
                    description = []
                elif line.startswith("Description:"):
                    description.append(line.split(":", 1)[1].strip())
                elif line.startswith("Threat Score:"):
                    try:
                        analysis[current_process]['threat_score'] = float(line.split(":")[1].strip())
                    except ValueError:
                        analysis[current_process]['threat_score'] = 'N/A'
                elif line.strip() and current_process:
                    description.append(line.strip())
            
            # Add the last process if exists
            if current_process:
                analysis[current_process]['description'] = ' '.join(description)
            
            rprint(f"[bold green]✓[/bold green] Parsed {len(analysis)} processes from Anthropic response")
            return analysis
        except Exception as e:
            rprint(f"[bold red]✗[/bold red] Error in Anthropic analysis: {e}")
            logging.error(f"Error in Anthropic analysis: {e}")
            logging.debug(f"Response content: {response.content}")
            return {}

def analyze_processes_ollama(processes_file):
    ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3")
    
    with open(processes_file, 'r') as f:
        processes = json.load(f)
    
    prompt = f"""You are a Windows security expert. Analyze the following list of Windows processes:

{json.dumps(processes, indent=2)}

For each process, provide a brief description of its typical function and assign a threat score from 0 (harmless) to 10 (highly suspicious). Consider the process name and path in your analysis. If you're uncertain about a process, state that clearly.

Respond with a JSON object where each key is the process name and the value is an object containing 'description' and 'threat_score' keys."""

    with console.status(f"[bold green]Analyzing processes with Ollama ({ollama_model})..."):
        try:
            response = http_session.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": ollama_model,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False
                },
                timeout=300  # 5 minutes timeout for processing all processes
            )
            response.raise_for_status()
            result = response.json()
            
            if 'response' in result:
                try:
                    analysis_text = result['response']
                    # Try to parse the response as JSON
                    try:
                        analysis = json.loads(analysis_text)
                    except json.JSONDecodeError:
                        # If JSON parsing fails, try to extract information manually
                        analysis = {}
                        current_process = None
                        for line in analysis_text.split('\n'):
                            if ':' in line:
                                key, value = line.split(':', 1)
                                key = key.strip()
                                value = value.strip()
                                if key == 'Process Name':
                                    current_process = value
                                    analysis[current_process] = {}
                                elif key == 'Description' and current_process:
                                    analysis[current_process]['description'] = value
                                elif key == 'Threat Score' and current_process:
                                    try:
                                        analysis[current_process]['threat_score'] = float(value)
                                    except ValueError:
                                        analysis[current_process]['threat_score'] = 'N/A'
                    
                    rprint(f"[bold green]✓[/bold green] Parsed {len(analysis)} processes from Ollama response")
                    return analysis
                except Exception as e:
                    rprint(f"[bold red]✗[/bold red] Failed to parse Ollama response: {e}")
                    logging.error(f"Failed to parse Ollama response: {e}")
                    return {}
            else:
                rprint("[bold red]✗[/bold red] Unexpected response format from Ollama")
                logging.error("Unexpected response format from Ollama")
                return {}
        except requests.RequestException as e:
            rprint(f"[bold red]✗[/bold red] Error in Ollama analysis: {e}")
            logging.error(f"Error in Ollama analysis: {e}")
            return {}
        except Exception as e:
            rprint(f"[bold red]✗[/bold red] Unexpected error in Ollama analysis: {e}")
            logging.error(f"Unexpected error in Ollama analysis: {e}")
            return {}

def analyze_processes_openai(processes_file):
    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    
    with open(processes_file, 'r') as f:
        processes = json.load(f)
    
    prompt = f"""Analyze the following list of Windows processes:

{json.dumps(processes, indent=2)}

For each process, provide a brief description of its typical function and assign a threat score from 0 (harmless) to 10 (highly suspicious). If you're uncertain about a process, state that clearly.

Format your response as follows for each process:
Process Name: [name]
Description: [brief description]
Threat Score: [score]

Do not include any other text or formatting."""

    with console.status(f"[bold green]Analyzing processes with OpenAI ({model})..."):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4000,
                temperature=0
            )
            
            content = response.choices[0].message.content
            
            # Parse the response content
            analysis = {}
            current_process = None
            description = []
            
            for line in content.split('\n'):
                if line.startswith("Process Name:"):
                    if current_process:
                        analysis[current_process]['description'] = ' '.join(description)
                    current_process = line.split(":")[1].strip()
                    analysis[current_process] = {}
                    description = []
                elif line.startswith("Description:"):
                    description.append(line.split(":", 1)[1].strip())
                elif line.startswith("Threat Score:"):
                    try:
                        analysis[current_process]['threat_score'] = float(line.split(":")[1].strip())
                    except ValueError:
                        analysis[current_process]['threat_score'] = 'N/A'
                elif line.strip() and current_process:
                    description.append(line.strip())
            
            # Add the last process if exists
            if current_process:
                analysis[current_process]['description'] = ' '.join(description)
            
            rprint(f"[bold green]✓[/bold green] Parsed {len(analysis)} processes from OpenAI response")
            return analysis
        except Exception as e:
            rprint(f"[bold red]✗[/bold red] Error in OpenAI analysis: {e}")
            logging.error(f"Error in OpenAI analysis: {e}")
            return {}

def generate_report(processes, analysis):
    report = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>AI Windows Process Analysis Report</title>
        <style>
            body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 1200px; margin: 0 auto; padding: 20px; }
            h1 { color: #2c3e50; }
            h2 { color: #34495e; font-size: 1.2em; margin-bottom: 5px; }
            .process-container { display: flex; flex-wrap: wrap; justify-content: space-between; }
            .process { background-color: #f9f9f9; border: 1px solid #ddd; padding: 15px; margin-bottom: 20px; border-radius: 5px; width: calc(50% - 20px); box-sizing: border-box; }
            .threat-score { font-weight: bold; }
            .threat-low { color: green; }
            .threat-medium { color: orange; }
            .threat-high { color: red; }
            .sort-controls { margin-bottom: 20px; }
            .sort-controls button { margin-right: 10px; margin-bottom: 5px; }
            .process p { word-wrap: break-word; overflow-wrap: break-word; }
            @media (max-width: 768px) {
                .process { width: 100%; }
            }
            @media (prefers-color-scheme: dark) {
                body { background-color: #1a1a1a; color: #f0f0f0; }
                .process { background-color: #2a2a2a; border-color: #444; }
                h1, h2 { color: #f0f0f0; }
            }
        </style>
        <script>
            let sortOrders = {
                'threat-score': 'desc',
                'name': 'asc',
                'user': 'asc',
                'status': 'asc',
                'cpu': 'desc',
                'memory': 'desc'
            };

            function sortProcesses(key) {
                let processes = Array.from(document.getElementsByClassName('process'));
                processes.sort((a, b) => {
                    let aVal = a.getAttribute('data-' + key);
                    let bVal = b.getAttribute('data-' + key);
                    if (['threat-score', 'cpu', 'memory'].includes(key)) {
                        aVal = parseFloat(aVal) || -1;
                        bVal = parseFloat(bVal) || -1;
                    }
                    if (sortOrders[key] === 'asc') {
                        return aVal > bVal ? 1 : aVal < bVal ? -1 : 0;
                    } else {
                        return aVal < bVal ? 1 : aVal > bVal ? -1 : 0;
                    }
                });
                let container = document.querySelector('.process-container');
                processes.forEach(process => container.appendChild(process));
                sortOrders[key] = sortOrders[key] === 'asc' ? 'desc' : 'asc';
                updateSortButtons(key);
            }

            function updateSortButtons(activeKey) {
                document.querySelectorAll('.sort-controls button').forEach(button => {
                    let key = button.getAttribute('data-sort');
                    button.textContent = `Sort by ${key.charAt(0).toUpperCase() + key.slice(1)} ${sortOrders[key] === 'asc' ? '▲' : '▼'}`;
                    button.style.fontWeight = (key === activeKey) ? 'bold' : 'normal';
                });
            }

            // Initial sort by threat score
            window.onload = function() {
                sortProcesses('threat-score');
            }
        </script>
    </head>
    <body>
        <h1>Windows Process Analysis Report</h1>
        <div class="sort-controls">
            <button onclick="sortProcesses('threat-score')" data-sort="threat-score">Sort by Threat Score ▼</button>
            <button onclick="sortProcesses('name')" data-sort="name">Sort by Process Name ▲</button>
            <button onclick="sortProcesses('user')" data-sort="user">Sort by User ▲</button>
            <button onclick="sortProcesses('status')" data-sort="status">Sort by Status ▲</button>
            <button onclick="sortProcesses('cpu')" data-sort="cpu">Sort by CPU Usage ▼</button>
            <button onclick="sortProcesses('memory')" data-sort="memory">Sort by Memory Usage ▼</button>
        </div>
        <div class="process-container">
    """
    
    for process in processes:
        process_analysis = analysis.get(process['name'], {})
        description = process_analysis.get('description', 'No analysis available')
        threat_score = process_analysis.get('threat_score', 'N/A')
        
        try:
            threat_score_num = float(threat_score)
            threat_score_display = f"{threat_score_num:.1f}"
            if threat_score_num < 4:
                threat_class = 'threat-low'
            elif threat_score_num < 7:
                threat_class = 'threat-medium'
            else:
                threat_class = 'threat-high'
        except ValueError:
            threat_score_num = -1
            threat_score_display = 'N/A'
            threat_class = 'threat-low'
        
        search_link = f"https://duckduckgo.com/?q={urllib.parse.quote(process['name'])}" if description == 'No analysis available' else ''
        
        report += f"""
        <div class="process" data-name="{process['name']}" data-user="{process['username']}" data-status="{process['status']}" data-threat-score="{threat_score_num}" data-cpu="{process['cpu_percent']}" data-memory="{process['memory_percent']}">
            <h2>{process['name']} (PID: {process['pid']})</h2>
            <p><strong>Executable:</strong> {process['exe']}</p>
            <p><strong>Status:</strong> {process['status']}</p>
            <p><strong>User:</strong> {process['username']}</p>
            <p><strong>CPU Usage:</strong> {process['cpu_percent']:.2f}%</p>
            <p><strong>Memory Usage:</strong> {process['memory_percent']:.2f}%</p>
            <p><strong>Analysis:</strong> {description.replace('Typical function:', '', 1).strip()}
            {'<a href="' + search_link + '" target="_blank">Search the web</a>' if search_link else ''}</p>
            <p class="threat-score {threat_class}">Threat Score: {threat_score_display}/10</p>
            <p><a href="file://{os.path.dirname(process['exe'])}">Open File Location</a></p>
        </div>
        """
    
    report += """
        </div>
        <script>
            updateSortButtons('threat-score');
        </script>
    </body>
    </html>
    """
    return report

def save_report(report):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    username = os.getenv("USERNAME")
    filename = f"process_report_{username}_{timestamp}.html"
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(report)
    
    logging.info(f"Report saved as {filename}")
    return filename

def main(ai_provider):
    try:
        rprint(Panel(f"[bold blue]Starting AI Process Report with {ai_provider.upper()}[/bold blue]"))
        
        with console.status("[bold green]Gathering processes...") as status:
            processes = get_processes()
            status.update(f"[bold green]Retrieved {len(processes)} processes (limit: {PROCESS_LIMIT})")
        
        processes_file = save_processes_to_file(processes)
        rprint(f"[bold green]✓[/bold green] Saved processes to {processes_file}")
        
        if ai_provider == 'ollama':
            analysis = analyze_processes_ollama(processes_file)
        elif ai_provider == 'anthropic':
            analysis = analyze_processes_anthropic(processes_file)
        elif ai_provider == 'openai':
            analysis = analyze_processes_openai(processes_file)
        else:
            rprint(f"[bold red]✗[/bold red] Unsupported AI provider: {ai_provider}")
            return
        
        rprint(f"[bold green]✓[/bold green] Analysis completed. Number of analyzed processes: {len(analysis)}")
        
        with console.status("[bold green]Generating report...") as status:
            report = generate_report(processes, analysis)
            filename = save_report(report)
            status.update(f"[bold green]Report generated and saved as {filename}")
        
        if filename:
            rprint(f"[bold green]✓[/bold green] Report saved as {filename}")
            rprint("[bold blue]Opening report in web browser...[/bold blue]")
            webbrowser.open(f'file://{os.path.abspath(filename)}')
        else:
            rprint("[bold red]✗[/bold red] Failed to save report")
    except Exception as e:
        rprint(f"[bold red]✗[/bold red] Critical error in main execution: {e}")
        logging.critical(f"Critical error in main execution: {e}")
        logging.debug(traceback.format_exc())

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Process Report")
    parser.add_argument('--ai', choices=['anthropic', 'openai', 'ollama'], 
                        default=AI_PROVIDER, 
                        help="Choose the AI model to use (overrides .env setting)")
    parser.add_argument('--debug', action='store_true', help="Enable debug mode")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.debug("Debug mode enabled")

    main(args.ai)