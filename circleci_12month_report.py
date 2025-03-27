import os
import requests
import time
import gzip
import shutil
import datetime
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Set your environment variables
ORG_ID = os.getenv('ORG_ID')
CIRCLE_TOKEN = os.getenv('CIRCLE_TOKEN')

# Create a directory for usage reports
REPORT_DIR = 'usage_reports'
os.makedirs(REPORT_DIR, exist_ok=True)

# Function to create a usage export job
def create_usage_export_job(org_id, circle_token, start_date, end_date):
    url = f"https://circleci.com/api/v2/organizations/{org_id}/usage_export_job"
    headers = {
        "Circle-Token": circle_token,
        "Content-Type": "application/json"
    }
    data = {
        "start": start_date,
        "end": end_date,
        "shared_org_ids": [org_id]
    }
    response = requests.post(url, headers=headers, json=data)
    
    if response.status_code != 201:
        print(f"Failed to create usage export job for {start_date} to {end_date}: {response.text}")
        return None
    
    return response.json().get('usage_export_job_id')

# Function to check the status of the usage export job
def check_job_status(org_id, circle_token, job_id):
    url = f"https://circleci.com/api/v2/organizations/{org_id}/usage_export_job/{job_id}"
    headers = {
        "Circle-Token": circle_token
    }
    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        print(f"Failed to get job status: {response.text}")
        return None
    
    return response.json()

# Function to download files from URLs
def download_files(download_urls, start_date, end_date, org_id):
    max_retries = 3
    downloaded_files = []
    
    for url in download_urls:
        print(f"Downloading {url}...")
        for attempt in range(1, max_retries + 1):
            response = requests.get(url, allow_redirects=True)
            if response.status_code == 200:
                # Create a structured filename
                filename = f"{org_id}_{start_date[:10]}_{end_date[:10]}.csv.gz"
                file_path = os.path.join(REPORT_DIR, filename)
                
                # Save the file
                with open(file_path, 'wb') as file:
                    file.write(response.content)
                print(f"Downloaded {file_path}")
                downloaded_files.append(file_path)
                break  # Exit retry loop on success
            else:
                print(f"Attempt {attempt}/{max_retries} failed to download {url}, Status Code: {response.status_code}")
                if attempt == max_retries:
                    print(f"Failed to download {url} after {max_retries} attempts.")
    
    return downloaded_files

# Function to validate file format
def validate_file(file_path):
    try:
        with open(file_path, 'rb') as file:
            # Check if the file starts with gzip magic number
            if file.read(2) != b'\x1f\x8b':
                print(f"File {file_path} is not a valid gzipped file.")
                return False
    except Exception as e:
        print(f"Error validating file {file_path}: {e}")
        return False
    return True

# Function to unzip downloaded files
def unzip_files(file_path, start_date, end_date, org_id):
    if validate_file(file_path):
        print(f"Unzipping {file_path}...")
        try:
            csv_filename = f"{start_date[:10]}_to_{end_date[:10]}_{org_id}.csv"
            output_path = os.path.join(REPORT_DIR, csv_filename)
            
            with gzip.open(file_path, 'rb') as f_in:
                with open(output_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            print(f"Unzipped to {output_path}")
            return output_path
        except Exception as e:
            print(f"Error unzipping {file_path}: {e}")
    return None

# Function to generate date ranges for the last 12 months in 30-day chunks
def generate_date_ranges():
    date_ranges = []
    
    # End with today
    end_date = datetime.datetime.now()
    
    # Start 12 months ago
    start_date = end_date - relativedelta(months=12)
    
    current_start = start_date
    
    # Create 30-day chunks
    while current_start < end_date:
        current_end = min(current_start + datetime.timedelta(days=30), end_date)
        
        # Format dates for API
        start_str = current_start.strftime("%Y-%m-%dT00:00:00Z")
        end_str = current_end.strftime("%Y-%m-%dT23:59:59Z")
        
        date_ranges.append((start_str, end_str))
        
        # Move to next chunk
        current_start = current_end
    
    return date_ranges

# Function to merge CSV files
def merge_csv_files(csv_files):
    if not csv_files:
        print("No CSV files to merge.")
        return None
    
    # Output merged file path
    merged_filename = f"merged_12_months_{datetime.datetime.now().strftime('%Y%m%d')}.csv"
    merged_path = os.path.join(REPORT_DIR, merged_filename)
    
    # Get headers from first file
    with open(csv_files[0], 'r', encoding='utf-8') as first_file:
        header = first_file.readline().strip()
    
    # Write merged file
    with open(merged_path, 'w', encoding='utf-8') as merged_file:
        # Write header
        merged_file.write(header + '\n')
        
        # Write data from each file, skipping headers
        for csv_file in csv_files:
            with open(csv_file, 'r', encoding='utf-8') as f:
                # Skip header
                next(f)
                # Write content
                for line in f:
                    merged_file.write(line)
    
    print(f"Merged all CSV files into {merged_path}")
    return merged_path

# Main function to process a single date range
def process_date_range(start_date, end_date):
    print(f"Processing date range: {start_date} to {end_date}")
    
    # Create the usage export job
    job_id = create_usage_export_job(ORG_ID, CIRCLE_TOKEN, start_date, end_date)
    
    if not job_id:
        print(f"Skipping date range {start_date} to {end_date} due to job creation failure.")
        return None
    
    print(f"Usage export job created with ID: {job_id}")
    
    # Poll for job status
    max_attempts = 20  # Increased for potentially longer jobs
    attempt = 0
    job_state = "processing"
    
    while job_state == "processing" and attempt < max_attempts:
        job_status = check_job_status(ORG_ID, CIRCLE_TOKEN, job_id)
        
        if job_status is None:
            break
        
        job_state = job_status.get('state')
        print(f"Job state: {job_state} (attempt {attempt+1}/{max_attempts})")
        
        if job_state == "processing":
            wait_time = min(30 * (attempt + 1), 300)  # Progressive backoff, max 5 minutes
            print(f"Job is still processing. Waiting for {wait_time} seconds before checking again...")
            time.sleep(wait_time)
        
        attempt += 1
    
    # Check if the job has completed
    if job_state == "completed":
        print("Job has completed. Downloading files...")
        download_urls = job_status.get('download_urls', [])
        downloaded_files = download_files(download_urls, start_date, end_date, ORG_ID)
        
        csv_files = []
        for file_path in downloaded_files:
            csv_file = unzip_files(file_path, start_date, end_date, ORG_ID)
            if csv_file:
                csv_files.append(csv_file)
        
        return csv_files
    else:
        print(f"Job has finished with state: {job_state}")
        if job_state == "processing":
            print("Max attempts reached. Job is still processing.")
        return None

# Main script execution
if __name__ == "__main__":
    print("Generating CircleCI usage reports for the last 12 months...")
    
    # Check if token is provided
    if not CIRCLE_TOKEN:
        exit("Please set CIRCLE_TOKEN in your environment variables.")
    
    if not ORG_ID:
        exit("Please set ORG_ID in your environment variables.")
    
    # Generate date ranges
    date_ranges = generate_date_ranges()
    print(f"Generated {len(date_ranges)} date ranges to cover the last 12 months")
    
    # Process each date range
    all_csv_files = []
    for i, (start_date, end_date) in enumerate(date_ranges):
        print(f"\nProcessing chunk {i+1}/{len(date_ranges)}")
        csv_files = process_date_range(start_date, end_date)
        if csv_files:
            all_csv_files.extend(csv_files)
        
        # Add a delay between API requests to avoid rate limiting
        if i < len(date_ranges) - 1:
            print("Waiting 10 seconds before processing next chunk...")
            time.sleep(10)
    
    # Merge all CSV files
    if all_csv_files:
        print("\nMerging all CSV files...")
        merged_file = merge_csv_files(all_csv_files)
        print(f"\nComplete! Final merged report is available at: {merged_file}")
    else:
        print("\nNo CSV files were generated. Please check the errors above.")
