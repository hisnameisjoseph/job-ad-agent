# Lambda container image, not a zip: litellm + instructor + pydantic exceed
# the 250MB unzipped limit for zip-packaged functions. Images allow 10GB.
FROM public.ecr.aws/lambda/python:3.12

# Dependencies first — this layer is cached unless requirements.txt changes,
# so ordinary code edits rebuild in seconds.
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY *.py ${LAMBDA_TASK_ROOT}/
COPY sources/ ${LAMBDA_TASK_ROOT}/sources/

# profile.yaml and companies.yaml are NOT copied in. They come from S3 at
# runtime so editing your profile does not require rebuilding and redeploying.
CMD ["lambda_handler.handler"]